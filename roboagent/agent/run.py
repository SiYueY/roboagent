"""A cancellable run with independent, non-blocking event subscriptions."""
from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4
from roboagent.agent.loop import run_loop
from roboagent.agent.types import AgentRunResult
from roboagent.runtime import (
    AgentCompletedEvent,
    AgentEvent,
    AgentStartedEvent,
    MessageCompletedEvent,
    MessageDeltaEvent,
    MessageStartedEvent,
    RuntimeErrorEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UserMessage,
)

logger = logging.getLogger(__name__)
_SENTINEL = object()

_EVENT_TYPES = {
    "agent_started": AgentStartedEvent,
    "agent_completed": AgentCompletedEvent,
    "turn_started": TurnStartedEvent,
    "turn_completed": TurnCompletedEvent,
    "message_started": MessageStartedEvent,
    "message_delta": MessageDeltaEvent,
    "message_completed": MessageCompletedEvent,
    "tool_started": ToolStartedEvent,
    "tool_completed": ToolCompletedEvent,
    "runtime_error": RuntimeErrorEvent,
}

class Cancellation:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None
    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "user") -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

class EventBroadcaster:
    def __init__(self, queue_size: int = 128) -> None:
        self._queue_size = queue_size
        self._queues: set[asyncio.Queue[AgentEvent | object]] = set()
        self._draining: set[asyncio.Queue[AgentEvent | object]] = set()
        self._closed = False

    def subscribe(self) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue(self._queue_size)
        if self._closed:
            queue.put_nowait(_SENTINEL)
        else:
            self._queues.add(queue)

        async def stream() -> AsyncIterator[AgentEvent]:
            try:
                while True:
                    if queue in self._draining and queue.empty():
                        return
                    item = await queue.get()
                    if item is _SENTINEL:
                        return
                    yield cast(AgentEvent, item)
            finally:
                self._queues.discard(queue)
                self._draining.discard(queue)

        return stream()

    def publish(self, event: AgentEvent) -> None:
        for queue in tuple(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._queues.discard(queue)
                self._draining.add(queue)
                logger.warning("disconnecting slow agent event subscriber")

    def close(self) -> None:
        self._closed = True
        for queue in tuple(self._queues):
            self._draining.add(queue)
        self._queues.clear()

@dataclass(slots=True)
class AgentRun:
    session: Any
    prompt: UserMessage
    run_id: str = field(default_factory=lambda: uuid4().hex)
    _token: Cancellation = field(default_factory=Cancellation, init=False)
    _broadcaster: EventBroadcaster = field(default_factory=EventBroadcaster, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _result: asyncio.Future[AgentRunResult] | None = field(default=None, init=False)
    _sequence: int = field(default=0, init=False)
    _terminal: bool = field(default=False, init=False)

    def events(self) -> AsyncIterator[AgentEvent]:
        self._start()
        return self._broadcaster.subscribe()

    def cancel(self, reason: str = "user") -> None:
        self._token.cancel(reason)

    async def result(self) -> AgentRunResult:
        self._start()
        assert self._result is not None
        return await self._result

    def _start(self) -> None:
        if self._task is None:
            self._result = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._execute())

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        event_type = _EVENT_TYPES[kind]
        event = event_type(run_id=self.run_id, sequence=self._sequence, **payload)
        self._broadcaster.publish(event)
        self.session._notify(event)

    async def _execute(self) -> None:
        agent = self.session.agent
        messages = list(self.session.messages)
        context_state = self.session.context_state
        messages.append(self.prompt)
        final = None
        status = "failed"
        error: str | None = None
        timeout_task: asyncio.Task[None] | None = None
        try:
            await self._emit("agent_started", {"session_id": self.session.session_id})
            await self._emit("message_started", {"turn": None, "message": self.prompt})
            await self._emit("message_completed", {"turn": None, "message": self.prompt})
            if agent.run_timeout is not None:
                timeout_task = asyncio.create_task(self._timeout_after(agent.run_timeout))
            tools = {tool.name: tool for tool in agent.tools}
            loop_result = await run_loop(
                model=agent.model,
                system_prompt=agent.system_prompt,
                messages=messages,
                tools=tools,
                definitions=tuple(tool.definition for tool in agent.tools),
                cancellation=self._token,
                emit=self._emit,
                run_id=self.run_id,
                max_turns=agent.max_turns,
                context_manager=agent.context_manager,
                context_state=context_state,
                transforms=agent.hooks.context_transforms,
                before_tool_call=agent.hooks.before_tool_call,
                after_tool_call=agent.hooks.after_tool_call,
            )
            final = loop_result.final_message
            status = loop_result.status
            error = loop_result.error
            context_state = loop_result.context_state
        except asyncio.CancelledError:
            if not self._token.cancelled:
                self._token.cancel("user")
            status = "timed_out" if self._token.reason == "timeout" else "cancelled"
            error = "Run timed out." if status == "timed_out" else "Run cancelled."
        except Exception:
            logger.exception("agent run failed")
            status, error = "failed", "Agent runtime failed."
        finally:
            if timeout_task:
                timeout_task.cancel()
        if self._token.cancelled and status not in {"timed_out", "cancelled"}:
            status = "timed_out" if self._token.reason == "timeout" else "cancelled"
            error = "Run timed out." if status == "timed_out" else "Run cancelled."
        result = AgentRunResult(tuple(messages), final, status, error, self.run_id)
        await self._emit("agent_completed", {"status": status, "error": error})
        self._terminal = True
        self._broadcaster.close()
        self.session._commit(messages, context_state)
        self.session._finish(self)
        assert self._result is not None
        self._result.set_result(result)

    async def _timeout_after(self, timeout: float) -> None:
        await asyncio.sleep(timeout)
        self._token.cancel("timeout")
