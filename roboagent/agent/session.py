"""Single-writer durable conversation Session and pending-input queue."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence
from uuid import uuid4

from roboagent.agent.types import RunConfig, RunResult
from roboagent.message import (
    AgentMessage,
    AssistantMessage,
    MediaLimits,
    ToolResultMessage,
    TranscriptValidator,
    UserMessage,
)
from roboagent.runtime.types import CancellationToken

if TYPE_CHECKING:
    from roboagent.agent.agent import Agent
    from roboagent.agent.run import Run


class SessionBusyError(RuntimeError):
    pass


class SessionClosedError(RuntimeError):
    pass


class SessionOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InputReceipt:
    input_id: str
    sequence: int
    session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.input_id, str) or not self.input_id or not isinstance(self.session_id, str) or not self.session_id or not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Invalid InputReceipt.")


@dataclass(frozen=True, slots=True)
class PendingInput:
    receipt: InputReceipt
    message: UserMessage
    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, InputReceipt) or not isinstance(self.message, UserMessage):
            raise TypeError("PendingInput requires canonical receipt and UserMessage.")
        if self.kind not in {"steer", "follow_up"}:
            raise ValueError("Invalid pending input kind.")


@dataclass(slots=True)
class Session:
    agent: Agent
    session_id: str
    _messages: list[AgentMessage] = field(default_factory=list, repr=False)
    _active_run_id: str | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)
    _pending: list[PendingInput] = field(default_factory=list, init=False, repr=False)
    _ownership_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _queue_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _transcript_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _media_limits: MediaLimits = field(init=False, repr=False)

    def __init__(self, agent: Agent, messages: Sequence[AgentMessage] = (), session_id: str | None = None) -> None:
        from roboagent.agent.agent import Agent as CanonicalAgent

        if not isinstance(agent, CanonicalAgent):
            raise TypeError("Session requires a canonical Agent.")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id must be non-empty or None.")
        self.agent = agent
        self.session_id = session_id or uuid4().hex
        self._messages = list(messages)
        self._active_run_id = None
        self._closed = False
        self._sequence = 0
        self._pending = []
        self._ownership_lock = threading.RLock()
        self._queue_lock = asyncio.Lock()
        self._transcript_lock = asyncio.Lock()
        self._media_limits = agent.media_limits
        TranscriptValidator(self._media_limits).validate(self._messages)

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return tuple(self._messages)

    @property
    def active_run_id(self) -> str | None:
        with self._ownership_lock:
            return self._active_run_id

    @property
    def closed(self) -> bool:
        with self._ownership_lock:
            return self._closed

    async def acquire_run(self, run_id: str) -> None:
        self._acquire_run_nowait(run_id)

    def _acquire_run_nowait(self, run_id: str) -> None:
        with self._ownership_lock:
            if self._closed:
                raise SessionClosedError("Session is closed.")
            if self._active_run_id is not None:
                raise SessionBusyError("Session already has an active Run.")
            self._active_run_id = run_id

    async def release_run(self, run_id: str) -> None:
        self._release_run_nowait(run_id)

    def _release_run_nowait(self, run_id: str) -> None:
        with self._ownership_lock:
            if self._active_run_id != run_id:
                raise SessionOwnershipError("Only the owning Run may release the Session.")
            self._active_run_id = None

    async def steer(self, message: UserMessage) -> InputReceipt:
        return await self._enqueue("steer", message)

    async def follow_up(self, message: UserMessage) -> InputReceipt:
        return await self._enqueue("follow_up", message)

    async def _enqueue(self, kind: str, message: UserMessage) -> InputReceipt:
        if not isinstance(message, UserMessage):
            raise TypeError("Only UserMessage may enter the pending input queue.")
        async with self._queue_lock:
            if self.closed:
                raise SessionClosedError("Session is closed.")
            self._sequence += 1
            receipt = InputReceipt(uuid4().hex, self._sequence, self.session_id)
            self._pending.append(PendingInput(receipt, message, kind))
            return receipt

    async def consume_pending(self, run_id: str, cancellation: CancellationToken) -> tuple[PendingInput, ...]:
        if self.active_run_id != run_id:
            raise SessionOwnershipError("Only the active Run may consume pending input.")
        async with self._queue_lock:
            async with self._transcript_lock:
                cancellation.raise_if_cancelled()
                pending = tuple(self._pending)
                if pending:
                    prospective = (*self._messages, *(item.message for item in pending))
                    TranscriptValidator(self._media_limits).validate(prospective)
                    self._messages.extend(item.message for item in pending)
                    self._pending.clear()
        return pending

    async def pending_inputs(self) -> tuple[PendingInput, ...]:
        async with self._queue_lock:
            return tuple(self._pending)

    def start(self, message: UserMessage | None = None, *, config: RunConfig | None = None) -> "Run":
        from roboagent.agent.run import Run

        if message is not None and not isinstance(message, UserMessage):
            raise TypeError("Session.start accepts UserMessage or None.")
        run = Run(self, config or self.agent.default_run_config)
        self._acquire_run_nowait(run.run_id)
        skill_bound = False
        message_appended = False
        if message is not None:
            try:
                TranscriptValidator(self._media_limits).validate((*self._messages, message))
            except BaseException:
                self._release_run_nowait(run.run_id)
                raise
        try:
            if self.agent.skill_manager is not None:
                run._skill_catalog = self.agent.skill_manager.bind_run(run.run_id)
                skill_bound = True
            if message is not None:
                self._messages.append(message)
                message_appended = True
            run.start_eager()
        except BaseException:
            if skill_bound and self.agent.skill_manager is not None:
                try:
                    self.agent.skill_manager.release_run(run.run_id)
                except Exception:
                    pass
            if message_appended:
                self._messages.pop()
            self._release_run_nowait(run.run_id)
            raise
        return run

    async def run(self, message: UserMessage | None = None, *, config: RunConfig | None = None) -> RunResult:
        return await self.start(message, config=config).result()

    async def commit_message(self, run_id: str, message: AssistantMessage) -> None:
        if self.active_run_id != run_id:
            raise SessionOwnershipError("Only the active Run may commit transcript facts.")
        async with self._transcript_lock:
            TranscriptValidator(self._media_limits).validate((*self._messages, message))
            self._messages.append(message)

    async def commit_exchange(self, run_id: str, assistant: AssistantMessage, results: tuple[ToolResultMessage, ...]) -> None:
        if self.active_run_id != run_id:
            raise SessionOwnershipError("Only the active Run may commit transcript facts.")
        block = (assistant, *results)
        async with self._transcript_lock:
            TranscriptValidator(self._media_limits).validate((*self._messages, *block))
            self._messages.extend(block)

    async def close(self) -> tuple[InputReceipt, ...]:
        with self._ownership_lock:
            if self._active_run_id is not None:
                raise SessionBusyError("Cannot close a Session with an active Run.")
            self._closed = True
        async with self._queue_lock:
            rejected = tuple(item.receipt for item in self._pending)
            self._pending.clear()
        return rejected
