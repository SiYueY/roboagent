"""Single-writer durable conversation Session and pending-input queue."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence, cast
from uuid import uuid4

from roboagent.agent.types import RunConfig, RunResult
from roboagent.message import (
    AgentMessage,
    AssistantMessage,
    MediaLimits,
    ToolResultMessage,
    TranscriptValidator,
    UserMessage,
    FrozenJsonObject,
    canonical_message_digest,
)
from roboagent.runtime.types import CancellationToken
from roboagent.runtime.event import RunEventEmitter

if TYPE_CHECKING:
    from roboagent.agent.agent import Agent
    from roboagent.agent.run import Run
    from roboagent.runtime import ExecutionScope, ExecutionTree, RuntimeCancellation
    from roboagent.context import CompactionUpdate, ContextSummary
    from roboagent.tool import (
        ArtifactDestination,
        ArtifactReader,
        ToolResultMaterializer,
        Workspace,
    )
    from roboagent.agent.persistence import SessionRepository, SessionSnapshot


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
        if (
            not isinstance(self.input_id, str)
            or not self.input_id
            or not isinstance(self.session_id, str)
            or not self.session_id
            or not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("Invalid InputReceipt.")


@dataclass(frozen=True, slots=True)
class PendingInput:
    receipt: InputReceipt
    message: UserMessage
    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, InputReceipt) or not isinstance(
            self.message, UserMessage
        ):
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
    _current_compaction: ContextSummary | None = field(
        default=None, init=False, repr=False
    )
    _ownership_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )
    _queue_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _transcript_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _compaction_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _media_limits: MediaLimits = field(init=False, repr=False)
    workspace: Workspace = field(init=False)
    result_materializer: ToolResultMaterializer = field(init=False)
    repository: SessionRepository | None = field(init=False, repr=False)
    metadata: FrozenJsonObject = field(init=False)
    artifact_reader: ArtifactReader = field(init=False, repr=False)
    artifact_destination: ArtifactDestination = field(init=False, repr=False)
    _root_session_id: str = field(init=False, repr=False)
    _runtime_revision: int = field(default=0, init=False, repr=False)
    _durable_revision: int | None = field(default=None, init=False, repr=False)
    _persist_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _event_emitter: RunEventEmitter | None = field(default=None, init=False, repr=False)

    def __init__(
        self,
        agent: Agent,
        messages: Sequence[AgentMessage] = (),
        session_id: str | None = None,
        *,
        workspace: Workspace | None = None,
        result_materializer: ToolResultMaterializer | None = None,
        repository: SessionRepository | None = None,
        metadata: FrozenJsonObject | None = None,
        allow_nondurable_artifacts: bool = False,
        artifact_reader: ArtifactReader | None = None,
        artifact_destination: ArtifactDestination | None = None,
    ) -> None:
        from roboagent.agent.agent import Agent as CanonicalAgent
        from roboagent.tool import (
            InMemoryWorkspace,
            InlineToolResultMaterializer,
            WorkspaceArtifactDestination,
            WorkspaceArtifactReader,
            WorkspaceToolResultMaterializer,
        )

        if not isinstance(agent, CanonicalAgent):
            raise TypeError("Session requires a canonical Agent.")
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id
        ):
            raise ValueError("session_id must be non-empty or None.")
        self.agent = agent
        self.session_id = session_id or uuid4().hex
        self._root_session_id = self.session_id
        self._messages = list(messages)
        self._active_run_id = None
        self._closed = False
        self._sequence = 0
        self._pending = []
        self._current_compaction = None
        self._ownership_lock = threading.RLock()
        self._queue_lock = asyncio.Lock()
        self._transcript_lock = asyncio.Lock()
        self._compaction_lock = asyncio.Lock()
        self._media_limits = agent.media_limits
        self.workspace = workspace or InMemoryWorkspace()
        self.result_materializer = result_materializer or InlineToolResultMaterializer()
        bound_workspace = getattr(self.result_materializer, "workspace", self.workspace)
        if bound_workspace is not self.workspace:
            raise ValueError(
                "ToolResultMaterializer must be bound to the Session Workspace."
            )
        if (
            repository is not None
            and isinstance(self.result_materializer, WorkspaceToolResultMaterializer)
            and not self.workspace.durable
            and not allow_nondurable_artifacts
        ):
            raise ValueError(
                "Persistent Sessions require a durable Workspace for artifact materialization."
            )
        self.repository = repository
        self.artifact_reader = cast(
            "ArtifactReader", artifact_reader or WorkspaceArtifactReader(self.workspace)
        )
        self.artifact_destination = cast(
            "ArtifactDestination",
            artifact_destination or WorkspaceArtifactDestination(self.workspace),
        )
        if not callable(getattr(self.artifact_reader, "iter_bytes", None)):
            raise TypeError("artifact_reader must implement iter_bytes().")
        if not callable(getattr(self.artifact_destination, "create_temp", None)):
            raise TypeError("artifact_destination must implement create_temp().")
        self.metadata = FrozenJsonObject(metadata or {})
        self._runtime_revision = 0
        self._durable_revision = None
        self._persist_lock = asyncio.Lock()
        self._event_emitter = None
        TranscriptValidator(self._media_limits).validate(self._messages)

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return tuple(self._messages)

    @property
    def current_compaction(self) -> ContextSummary | None:
        return self._current_compaction

    @property
    def runtime_revision(self) -> int:
        return self._runtime_revision

    @property
    def durable_revision(self) -> int | None:
        return self._durable_revision

    async def capture_context_state(
        self, run_id: str
    ) -> tuple[tuple[AgentMessage, ...], ContextSummary | None]:
        """Capture immutable context inputs without exposing Session to a manager."""
        if self.active_run_id != run_id:
            raise SessionOwnershipError(
                "Only the active Run may capture context state."
            )
        async with self._transcript_lock:
            async with self._compaction_lock:
                return tuple(self._messages), self._current_compaction

    async def commit_compaction(self, run_id: str, update: CompactionUpdate) -> bool:
        """Commit derived working context if it was prepared from the current state."""
        from roboagent.context import CompactionUpdate

        if self.active_run_id != run_id:
            raise SessionOwnershipError(
                "Only the active Run may commit compaction state."
            )
        if not isinstance(update, CompactionUpdate):
            raise TypeError("update must be CompactionUpdate.")
        async with self._compaction_lock:
            current_digest = (
                None
                if self._current_compaction is None
                else self._current_compaction.source_digest
            )
            if current_digest != update.expected_summary_digest:
                return False
            self._current_compaction = update.summary
            self._runtime_revision += 1
            revision = self._runtime_revision
        await self._persist_required(revision)
        return True

    @property
    def active_run_id(self) -> str | None:
        with self._ownership_lock:
            return self._active_run_id

    @property
    def closed(self) -> bool:
        with self._ownership_lock:
            return self._closed

    async def acquire_run(
        self, run_id: str, events: RunEventEmitter | None = None
    ) -> None:
        self._acquire_run_nowait(run_id, events)

    def _acquire_run_nowait(
        self, run_id: str, events: RunEventEmitter | None = None
    ) -> None:
        with self._ownership_lock:
            if self._closed:
                raise SessionClosedError("Session is closed.")
            if self._active_run_id is not None:
                raise SessionBusyError("Session already has an active Run.")
            self._active_run_id = run_id
            self._event_emitter = events

    async def release_run(self, run_id: str) -> None:
        self._release_run_nowait(run_id)

    def _release_run_nowait(self, run_id: str) -> None:
        with self._ownership_lock:
            if self._active_run_id != run_id:
                raise SessionOwnershipError(
                    "Only the owning Run may release the Session."
                )
            self._active_run_id = None
            self._event_emitter = None

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
            self._runtime_revision += 1
            revision = self._runtime_revision
        await self._persist_required(revision)
        return receipt

    async def consume_pending(
        self, run_id: str, cancellation: CancellationToken
    ) -> tuple[PendingInput, ...]:
        if self.active_run_id != run_id:
            raise SessionOwnershipError(
                "Only the active Run may consume pending input."
            )
        async with self._queue_lock:
            async with self._transcript_lock:
                cancellation.raise_if_cancelled()
                pending = tuple(self._pending)
                if pending:
                    prospective = (*self._messages, *(item.message for item in pending))
                    TranscriptValidator(self._media_limits).validate(prospective)
                    self._messages.extend(item.message for item in pending)
                    self._pending.clear()
                    self._runtime_revision += 1
                    revision = self._runtime_revision
                else:
                    revision = None
        if revision is not None:
            await self._persist_required(revision)
        return pending

    async def _commit_initial_input(
        self,
        run_id: str,
        message: UserMessage | None,
        pending_through_sequence: int,
        cancellation: CancellationToken,
    ) -> tuple[PendingInput, ...]:
        if self.active_run_id != run_id:
            raise SessionOwnershipError("Only the active Run may commit initial input.")
        async with self._queue_lock:
            async with self._transcript_lock:
                cancellation.raise_if_cancelled()
                count = 0
                for item in self._pending:
                    if item.receipt.sequence > pending_through_sequence:
                        break
                    count += 1
                pending = tuple(self._pending[:count])
                block = (
                    *(item.message for item in pending),
                    *((message,) if message is not None else ()),
                )
                if block:
                    prospective = (*self._messages, *block)
                    TranscriptValidator(self._media_limits).validate(prospective)
                    self._messages.extend(block)
                    del self._pending[:count]
                    self._runtime_revision += 1
                    revision = self._runtime_revision
                else:
                    revision = None
        if revision is not None:
            await self._persist_required(revision)
        return pending

    async def pending_inputs(self) -> tuple[PendingInput, ...]:
        async with self._queue_lock:
            return tuple(self._pending)

    def start(
        self, message: UserMessage | None = None, *, config: RunConfig | None = None
    ) -> "Run":
        from roboagent.agent.run import Run

        if message is not None and not isinstance(message, UserMessage):
            raise TypeError("Session.start accepts UserMessage or None.")
        run = Run(self, config or self.agent.default_run_config)
        self._acquire_run_nowait(run.run_id, run._events)
        run._initial_message = message
        run._initial_pending_sequence = self._sequence
        skill_bound = False
        if message is not None:
            try:
                TranscriptValidator(self._media_limits).validate(
                    (*self._messages, message)
                )
            except BaseException:
                self._release_run_nowait(run.run_id)
                raise
        try:
            if self.agent.skill_manager is not None:
                run._skill_catalog = self.agent.skill_manager.bind_run(run.run_id)
                skill_bound = True
            run.start_eager()
        except BaseException:
            if skill_bound and self.agent.skill_manager is not None:
                try:
                    self.agent.skill_manager.release_run(run.run_id)
                except Exception:
                    pass
            self._release_run_nowait(run.run_id)
            raise
        return run

    async def run(
        self, message: UserMessage | None = None, *, config: RunConfig | None = None
    ) -> RunResult:
        return await self.start(message, config=config).result()

    def _start_nested(
        self,
        message: UserMessage,
        *,
        config: RunConfig,
        tree: ExecutionTree,
        scope: ExecutionScope,
        events: RunEventEmitter,
        cancellation: RuntimeCancellation,
        output_processor,
    ) -> "Run":
        from roboagent.agent.run import Run

        run = Run(self, config, run_id=scope.lineage.execution_run_id)
        run._attach_nested(
            tree=tree,
            scope=scope,
            events=events,
            cancellation=cancellation,
            output_processor=output_processor,
        )
        self._acquire_run_nowait(run.run_id, events)
        run._initial_message = message
        run._initial_pending_sequence = self._sequence
        try:
            TranscriptValidator(self._media_limits).validate((*self._messages, message))
            if self.agent.skill_manager is not None:
                run._skill_catalog = self.agent.skill_manager.bind_run(run.run_id)
            run.start_eager()
        except BaseException:
            if self.agent.skill_manager is not None and run._skill_catalog is not None:
                try:
                    self.agent.skill_manager.release_run(run.run_id)
                except Exception:
                    pass
            self._release_run_nowait(run.run_id)
            raise
        return run

    async def commit_message(self, run_id: str, message: AssistantMessage) -> None:
        if self.active_run_id != run_id:
            raise SessionOwnershipError(
                "Only the active Run may commit transcript facts."
            )
        async with self._transcript_lock:
            TranscriptValidator(self._media_limits).validate((*self._messages, message))
            self._messages.append(message)
            self._runtime_revision += 1
            revision = self._runtime_revision
        await self._persist_required(revision)

    async def commit_exchange(
        self,
        run_id: str,
        assistant: AssistantMessage,
        results: tuple[ToolResultMessage, ...],
    ) -> None:
        if self.active_run_id != run_id:
            raise SessionOwnershipError(
                "Only the active Run may commit transcript facts."
            )
        block: tuple[AgentMessage, ...] = (assistant, *results)
        async with self._transcript_lock:
            TranscriptValidator(self._media_limits).validate((*self._messages, *block))
            self._messages.extend(block)
            self._runtime_revision += 1
            revision = self._runtime_revision
        await self._persist_required(revision)

    async def snapshot(self) -> SessionSnapshot:
        from roboagent.agent.persistence import SCHEMA_VERSION, SessionSnapshot

        async with self._queue_lock:
            async with self._transcript_lock:
                async with self._compaction_lock:
                    return SessionSnapshot(
                        SCHEMA_VERSION,
                        self.session_id,
                        self._runtime_revision,
                        self._sequence,
                        tuple(self._messages),
                        tuple(self._pending),
                        self._current_compaction,
                        self.metadata,
                    )

    async def persist(self) -> int:
        return await self._persist_latest(self._runtime_revision)

    async def _persist_required(self, revision: int) -> None:
        if self.repository is not None:
            await self._persist_latest(revision)

    async def _persist_latest(self, required_revision: int) -> int:
        from roboagent.agent.persistence import SessionPersistenceError

        if self.repository is None:
            raise SessionPersistenceError("Session has no repository.")
        async with self._persist_lock:
            if (
                self._durable_revision is not None
                and self._durable_revision >= required_revision
            ):
                return self._durable_revision
            try:
                snapshot = await self.snapshot()
                expected = self._durable_revision
                persisted = await self.repository.save(
                    snapshot, expected_revision=expected
                )
                if persisted != snapshot.revision:
                    raise SessionPersistenceError(
                        "Repository returned an unexpected revision."
                    )
            except SessionPersistenceError as exc:
                await self._emit_persistence_event(
                    "session.persistence_failed",
                    required_revision=required_revision,
                    error_code=exc.code,
                )
                raise
            except Exception as exc:
                error = SessionPersistenceError("Session repository save failed.")
                await self._emit_persistence_event(
                    "session.persistence_failed",
                    required_revision=required_revision,
                    error_code=error.code,
                )
                raise error from exc
            self._durable_revision = persisted
            await self._emit_persistence_event("session.persisted", revision=persisted)
            return persisted

    async def _emit_persistence_event(self, event_type: str, **payload: object) -> None:
        emitter = self._event_emitter
        if emitter is not None:
            await emitter.emit(event_type, **payload)  # type: ignore[arg-type]

    @classmethod
    def restore(
        cls,
        *,
        agent: Agent,
        snapshot: SessionSnapshot,
        repository: SessionRepository | None = None,
        workspace: Workspace | None = None,
        result_materializer: ToolResultMaterializer | None = None,
        allow_nondurable_artifacts: bool = False,
        artifact_reader: ArtifactReader | None = None,
        artifact_destination: ArtifactDestination | None = None,
    ) -> "Session":
        from roboagent.agent.persistence import (
            SCHEMA_VERSION,
            SessionCorruptedError,
            SessionSnapshot,
            SessionVersionUnsupportedError,
        )

        if not isinstance(snapshot, SessionSnapshot):
            raise TypeError("snapshot must be SessionSnapshot.")
        if snapshot.schema_version != SCHEMA_VERSION:
            raise SessionVersionUnsupportedError(
                "Unsupported Session snapshot version."
            )
        TranscriptValidator(agent.media_limits).validate(snapshot.messages)
        if not all(isinstance(item, PendingInput) for item in snapshot.pending):
            raise SessionCorruptedError("Snapshot pending values are not canonical.")
        sequences = [item.receipt.sequence for item in snapshot.pending]
        if any(
            item.receipt.session_id != snapshot.session_id for item in snapshot.pending
        ):
            raise SessionCorruptedError("Pending input belongs to another Session.")
        if (
            sequences != sorted(sequences)
            or len(sequences) != len(set(sequences))
            or any(
                left >= right
                for left, right in zip(sequences, sequences[1:], strict=False)
            )
        ):
            raise SessionCorruptedError(
                "Pending input sequence is not strictly increasing."
            )
        if sequences and snapshot.last_pending_sequence < max(sequences):
            raise SessionCorruptedError("last_pending_sequence is stale.")
        session = cls(
            agent,
            snapshot.messages,
            snapshot.session_id,
            workspace=workspace,
            result_materializer=result_materializer,
            repository=repository,
            metadata=snapshot.metadata,
            allow_nondurable_artifacts=allow_nondurable_artifacts,
            artifact_reader=artifact_reader,
            artifact_destination=artifact_destination,
        )
        session._pending = list(snapshot.pending)
        session._sequence = snapshot.last_pending_sequence
        compaction = snapshot.compaction
        if compaction is not None:
            end = compaction.source_end_exclusive
            try:
                TranscriptValidator(agent.media_limits).validate(
                    snapshot.messages[:end]
                )
            except Exception:
                compaction = None
            if compaction is not None and (
                end > len(snapshot.messages)
                or canonical_message_digest(snapshot.messages[:end])
                != compaction.source_digest
            ):
                compaction = None
        session._current_compaction = compaction
        session._runtime_revision = snapshot.revision
        session._durable_revision = snapshot.revision
        session._active_run_id = None
        session._event_emitter = None
        return session

    async def set_metadata(self, metadata: FrozenJsonObject) -> None:
        value = FrozenJsonObject(metadata)
        if value == self.metadata:
            return
        self.metadata = value
        self._runtime_revision += 1
        await self._persist_required(self._runtime_revision)

    async def close(self) -> tuple[InputReceipt, ...]:
        """Close this runtime handle without changing durable Session truth."""
        with self._ownership_lock:
            if self._active_run_id is not None:
                raise SessionBusyError("Cannot close a Session with an active Run.")
            self._closed = True
        async with self._queue_lock:
            return tuple(item.receipt for item in self._pending)
