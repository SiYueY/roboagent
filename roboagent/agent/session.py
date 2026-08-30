"""Conversation state and session-level synchronization."""
from __future__ import annotations
import asyncio, logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import uuid4
from roboagent.agent.agent import Agent
from roboagent.agent.types import Observer
from roboagent.runtime import AgentEvent, AgentRunResult, Message, UserMessage

_LOG = logging.getLogger(__name__)
class SessionBusyError(RuntimeError): pass
@dataclass(slots=True)
class AgentSession:
    agent: Agent; messages: list[Message]; session_id: str
    _active: bool = field(default=False, init=False, repr=False); _observers: set[Observer] = field(default_factory=set, init=False, repr=False)
    def __init__(self, agent: Agent, messages: Sequence[Message] = (), session_id: str | None = None) -> None:
        self.agent,self.messages,self.session_id,self._active,self._observers=agent,list(messages),session_id or uuid4().hex,False,set()
    def subscribe(self, observer: Observer) -> Callable[[], None]:
        self._observers.add(observer); return lambda: self._observers.discard(observer)
    def start(self, prompt: str | UserMessage):
        if self._active: raise SessionBusyError("This session already has an active run.")
        self._active=True
        from roboagent.agent.run import AgentRun
        return AgentRun(self, prompt if isinstance(prompt,UserMessage) else UserMessage(prompt))
    async def run(self, prompt: str | UserMessage) -> AgentRunResult: return await self.start(prompt).result()
    async def _notify(self, event: AgentEvent) -> None:
        for observer in tuple(self._observers):
            try:
                value=observer(event)
                if asyncio.iscoroutine(value): await value
            except Exception: _LOG.exception("Agent session observer failed")
    def _finish(self) -> None: self._active=False
