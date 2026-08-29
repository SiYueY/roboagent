"""Stateful facade around the framework-independent agent loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from roboagent.agent.hooks import AfterToolCall, BeforeToolCall, ContextTransform
from roboagent.agent.loop import run_loop
from roboagent.model.client import ChatModel
from roboagent.runtime import AgentEndEvent, AgentEvent, AgentRunResult, AgentStartEvent, Message, MessageEvent, UserMessage
from roboagent.tool import Tool

Observer = Callable[[AgentEvent], object]
_LOG = logging.getLogger(__name__)


class AgentAlreadyRunningError(RuntimeError): pass


class _Cancellation:
    def __init__(self) -> None: self.event = asyncio.Event()
    @property
    def cancelled(self) -> bool: return self.event.is_set()


@dataclass(slots=True)
class Agent:
    model: ChatModel
    tools: Sequence[Tool] = ()
    system_prompt: str | None = None
    context_transforms: Sequence[ContextTransform] = ()
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    max_turns: int = 32
    messages: list[Message] = field(default_factory=list)
    _observers: set[Observer] = field(default_factory=set, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _cancellation: _Cancellation | None = field(default=None, init=False, repr=False)

    def subscribe(self, observer: Observer) -> Callable[[], None]:
        self._observers.add(observer)
        return lambda: self._observers.discard(observer)

    def cancel(self) -> bool:
        if self._cancellation is None: return False
        self._cancellation.event.set()
        return True

    async def run(self, prompt: str | UserMessage) -> AgentRunResult:
        result = None
        async for event in self.stream(prompt):
            if isinstance(event, AgentEndEvent): result = event.result
        assert result is not None
        return result

    async def stream(self, prompt: str | UserMessage) -> AsyncIterator[AgentEvent]:
        if self._lock.locked(): raise AgentAlreadyRunningError("This Agent already has an active run.")
        await self._lock.acquire()
        run_id, cancellation = uuid4().hex, _Cancellation()
        self._cancellation = cancellation
        working = list(self.messages)
        user = prompt if isinstance(prompt, UserMessage) else UserMessage(prompt)
        working.append(user)
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        async def emit(event: AgentEvent) -> None:
            await queue.put(event)
            for observer in tuple(self._observers):
                try:
                    value = observer(event)
                    if asyncio.iscoroutine(value): await value
                except Exception:
                    _LOG.exception("Agent observer failed")
        async def execute() -> None:
            try:
                await emit(AgentStartEvent(run_id)); await emit(MessageEvent(user, phase="start")); await emit(MessageEvent(user, phase="end"))
                final, status, error = await run_loop(model=self.model, system_prompt=self.system_prompt, messages=working, tools=self.tools,
                    cancellation=cancellation, emit=emit, run_id=run_id, max_turns=self.max_turns, transforms=self.context_transforms,
                    before_tool_call=self.before_tool_call, after_tool_call=self.after_tool_call)
            except Exception as exc:
                final, status, error = None, "failed", str(exc)
            self.messages.extend(working[len(self.messages):])
            await emit(AgentEndEvent(AgentRunResult(tuple(working), final, status, error, run_id)))
        task = asyncio.create_task(execute())
        try:
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, AgentEndEvent): break
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._cancellation = None; self._lock.release()
