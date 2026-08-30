"""Single cancellable execution of an AgentSession."""
from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4
from roboagent.agent.loop import run_loop
from roboagent.agent.session import AgentSession
from roboagent.runtime import AgentEndEvent, AgentEvent, AgentRunResult, AgentStartEvent, MessageEvent, UserMessage

class Cancellation:
    def __init__(self) -> None: self._event=asyncio.Event()
    @property
    def cancelled(self) -> bool: return self._event.is_set()
    def cancel(self) -> None: self._event.set()
@dataclass(slots=True)
class AgentRun(AsyncIterator[AgentEvent]):
    session: AgentSession; prompt: UserMessage; run_id: str = field(default_factory=lambda:uuid4().hex)
    _token: Cancellation = field(default_factory=Cancellation, init=False); _queue: asyncio.Queue[AgentEvent] = field(default_factory=lambda:asyncio.Queue(maxsize=128), init=False); _task: asyncio.Task[None] | None = field(default=None,init=False); _result: asyncio.Future[AgentRunResult] | None = field(default=None,init=False); _ended: bool = field(default=False,init=False)
    def cancel(self) -> None:
        self._token.cancel()
        if self._task and not self._task.done(): self._task.cancel()
    def __aiter__(self) -> AsyncIterator[AgentEvent]: self._start(); return self
    async def __anext__(self) -> AgentEvent:
        if self._ended: raise StopAsyncIteration
        self._start(); event=await self._queue.get()
        if isinstance(event,AgentEndEvent): self._ended=True
        return event
    async def result(self) -> AgentRunResult:
        self._start(); assert self._result is not None; return await self._result
    def _start(self) -> None:
        if self._task is None:
            self._result=asyncio.get_running_loop().create_future(); self._task=asyncio.create_task(self._execute())
    async def _emit(self,event: AgentEvent) -> None:
        await self._queue.put(event); await self.session._notify(event)
    async def _execute(self) -> None:
        working=list(self.session.messages)+[self.prompt]; final=None; status="failed"; error: str|None=None
        try:
            await self._emit(AgentStartEvent(self.run_id)); await self._emit(MessageEvent(self.prompt,phase="start")); await self._emit(MessageEvent(self.prompt,phase="end")); agent=self.session.agent
            final,status,error=await run_loop(model=agent.model,system_prompt=agent.system_prompt,messages=working,tools=agent.tools,cancellation=self._token,emit=self._emit,run_id=self.run_id,max_turns=agent.max_turns,transforms=agent.hooks.context_transforms,before_tool_call=agent.hooks.before_tool_call,after_tool_call=agent.hooks.after_tool_call)
        except asyncio.CancelledError: status,error="cancelled","Run cancelled."
        except Exception as exc: status,error="failed",str(exc)
        result=AgentRunResult(tuple(working),final,status,error,self.run_id); self.session.messages.extend(working[len(self.session.messages):]); assert self._result is not None; self._result.set_result(result)
        try: await self._emit(AgentEndEvent(result))
        finally: self.session._finish()
