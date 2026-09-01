"""Conversation state and session-level run exclusion."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from roboagent.agent.agent import Agent
from roboagent.context import SessionContextState
from roboagent.runtime import AgentEvent, Message, UserMessage

logger = logging.getLogger(__name__)
Observer = Callable[[AgentEvent], object]


class SessionBusyError(RuntimeError):
    """Raised when code starts a second run in the same session."""


@dataclass(slots=True)
class AgentSession:
    """Mutable transcript and best-effort observers for one conversation."""

    agent: Agent
    messages: list[Message]
    session_id: str
    context_state: SessionContextState
    _active: bool = field(default=False, init=False, repr=False)
    _observers: set[Observer] = field(default_factory=set, init=False, repr=False)

    def __init__(
        self,
        agent: Agent,
        messages: Sequence[Message] = (),
        session_id: str | None = None,
        context_state: SessionContextState | None = None,
    ) -> None:
        self.agent = agent
        self.messages = list(messages)
        self.session_id = session_id or uuid4().hex
        self.context_state = context_state or SessionContextState()
        self._active = False
        self._observers = set()

    def subscribe(self, observer: Observer) -> Callable[[], None]:
        self._observers.add(observer)

        def unsubscribe() -> None:
            self._observers.discard(observer)

        return unsubscribe

    def start(self, prompt: str | UserMessage) -> "AgentRun":
        if self._active:
            raise SessionBusyError("This session already has an active run.")
        self._active = True
        from roboagent.agent.run import AgentRun

        message = prompt if isinstance(prompt, UserMessage) else UserMessage(prompt)
        return AgentRun(self, message)

    async def run(self, prompt: str | UserMessage):
        return await self.start(prompt).result()

    def _notify(self, event: AgentEvent) -> None:
        for observer in tuple(self._observers):
            try:
                outcome = observer(event)
                if inspect.isawaitable(outcome):
                    task = asyncio.create_task(outcome)
                    task.add_done_callback(_log_observer_failure)
            except Exception:
                logger.exception("agent session observer failed")

    def _commit(self, messages: list[Message], context_state: SessionContextState) -> None:
        self.messages[:] = messages
        self.context_state = context_state

    def _finish(self, _run: object) -> None:
        self._active = False


def _log_observer_failure(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("agent session observer failed")
