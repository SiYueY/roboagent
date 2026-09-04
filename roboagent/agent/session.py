"""Session owns the only canonical transcript and one active V1 run."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Sequence
from uuid import uuid4

from roboagent.agent.types import RunConfig, RunResult
from roboagent.message import Message, ProtocolError, TranscriptValidator, UserMessage
from roboagent.runtime.types import MediaLimits

class SessionBusyError(RuntimeError): pass
class InvalidContinuationError(ProtocolError): pass

@dataclass(slots=True)
class AgentSession:
    agent: object
    session_id: str
    _messages: list[Message] = field(default_factory=list)
    _media_limits: MediaLimits = field(init=False, repr=False)
    _active: object | None = field(default=None, init=False, repr=False)
    def __init__(self, agent: object, messages: Sequence[Message] = (), session_id: str | None = None) -> None:
        self.agent = agent; self.session_id = session_id or uuid4().hex; self._messages = list(messages); self._active = None
        self._media_limits = agent.media_limits
        TranscriptValidator(self._media_limits).validate(self._messages)
    @property
    def messages(self) -> tuple[Message, ...]: return tuple(self._messages)
    def start(self, prompt: str | UserMessage, *, config: RunConfig | None = None, metadata: dict[str, object] | None = None) -> "AgentRun":
        if self._active is not None: raise SessionBusyError("Session already has an active run.")
        message = prompt if isinstance(prompt, UserMessage) else UserMessage(prompt, limits=self._media_limits)
        # Validate prospective complete history before mutating, then atomically own and commit.
        TranscriptValidator(self._media_limits).validate((*self._messages, message))
        from .run import AgentRun
        run = AgentRun(self, config or self.agent.default_run_config, metadata or {})
        self._active = run; self._messages.append(message)
        try:
            run.start_eager()
        except BaseException:
            self._messages.pop()
            self._active = None
            raise
        return run
    async def run(self, prompt: str | UserMessage, *, config: RunConfig | None = None, metadata: dict[str, object] | None = None) -> RunResult:
        return await self.start(prompt, config=config, metadata=metadata).result()
    def continue_run(self, *, config: RunConfig | None = None, metadata: dict[str, object] | None = None) -> "AgentRun":
        if self._active is not None: raise SessionBusyError("Session already has an active run.")
        if not self._messages: raise InvalidContinuationError("Cannot continue an empty Session.")
        try: TranscriptValidator(self._media_limits).validate(self._messages)
        except ProtocolError as exc: raise InvalidContinuationError(str(exc)) from exc
        from .run import AgentRun
        run = AgentRun(self, config or self.agent.default_run_config, metadata or {})
        self._active = run
        try:
            run.start_eager()
        except BaseException:
            self._active = None
            raise
        return run
    def _append(self, message: Message) -> None:
        TranscriptValidator(self._media_limits).validate((*self._messages, message), complete=False)
        self._messages.append(message)
    def _finish(self, run: object) -> None:
        if self._active is run: self._active = None
        TranscriptValidator(self._media_limits).validate(self._messages)
