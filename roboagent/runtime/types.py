"""Provider-independent runtime control and observation types."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from roboagent.message import (
    ArtifactReferenceContent,
    AudioContent,
    BytesSource,
    FileContent,
    ImageContent,
    JsonContent,
    MediaSource,
    MessageContent,
    TextContent,
    _mime,
    _source,
)

if TYPE_CHECKING:
    from .execution import RunExecutionContext

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def safe_error_code(value: str) -> str:
    if not isinstance(value, str) or not _ERROR_CODE.fullmatch(value):
        raise ValueError("Error code must be a safe identifier.")
    return value


class CancellationReason(Enum):
    USER = "user"
    TIMEOUT = "timeout"


class CancellationOrigin(Enum):
    PARENT = "parent"
    TIMEOUT = "timeout"
    EXTERNAL = "external"
    RUNTIME = "runtime"


class RuntimeCancellation:
    """Small cooperative cancellation token used by the runtime."""

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = asyncio.Event()
        self._reason: CancellationReason | None = None
        self._origin: CancellationOrigin | None = None
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or bool(
            self._parent is not None and self._parent.cancelled
        )

    @property
    def reason(self) -> CancellationReason | None:
        return self._reason or getattr(self._parent, "reason", None)

    @property
    def origin(self) -> CancellationOrigin | None:
        if self._event.is_set():
            return self._origin
        return (
            CancellationOrigin.PARENT
            if self._parent is not None and self._parent.cancelled
            else None
        )

    def cancel(
        self,
        reason: CancellationReason = CancellationReason.USER,
        origin: CancellationOrigin = CancellationOrigin.EXTERNAL,
    ) -> None:
        if not self.cancelled:
            self._reason = reason
            self._origin = (
                CancellationOrigin.TIMEOUT
                if reason is CancellationReason.TIMEOUT
                else origin
            )
            self._event.set()

    async def wait_cancelled(self) -> None:
        if self.cancelled:
            return
        local = asyncio.create_task(self._event.wait())
        if self._parent is None:
            await local
            return
        parent = asyncio.create_task(self._parent.wait_cancelled())
        try:
            await asyncio.wait({local, parent}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (local, parent):
                if not task.done():
                    task.cancel()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(
                self._reason.value if self._reason else "cancelled"
            )


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...

    async def wait_cancelled(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    session_id: str
    cancellation: CancellationToken
    execution: "RunExecutionContext | None" = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run_id, str)
            or not self.run_id
            or not isinstance(self.session_id, str)
            or not self.session_id
        ):
            raise ValueError("RunContext requires run_id and session_id.")
        if not all(
            hasattr(self.cancellation, name)
            for name in ("cancelled", "raise_if_cancelled", "wait_cancelled")
        ):
            raise TypeError("RunContext requires a CancellationToken.")
        if self.execution is not None:
            if self.run_id != self.execution.lineage.execution_run_id:
                raise ValueError("RunContext.run_id must match execution lineage.")
            if self.cancellation is not self.execution.cancellation:
                raise ValueError(
                    "RunContext cancellation must be the execution cancellation view."
                )


class RunStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunPhase(Enum):
    IDLE = "idle"
    PREPARING_CONTEXT = "preparing_context"
    MODEL = "model"
    TOOL = "tool"
    BETWEEN_TURNS = "between_turns"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    message: str
    retryable: bool = False
    cause_type: str | None = None

    def __post_init__(self) -> None:
        safe_error_code(self.code)
        if (
            not isinstance(self.message, str)
            or not self.message
            or not isinstance(self.retryable, bool)
        ):
            raise TypeError("Invalid RunError metadata.")
        if self.cause_type is not None and not isinstance(self.cause_type, str):
            raise TypeError("RunError.cause_type must be str or None.")


class Modality(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"


def modality(value: MessageContent) -> Modality:
    if isinstance(value, (TextContent, JsonContent)):
        return Modality.TEXT
    if isinstance(value, ArtifactReferenceContent):
        return Modality.FILE
    if isinstance(value, ImageContent):
        return Modality.IMAGE
    if isinstance(value, AudioContent):
        return Modality.AUDIO
    if isinstance(value, FileContent):
        return Modality.FILE
    raise TypeError(f"Unsupported content: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ContentSummary:
    modality: Modality
    media_type: str | None = None
    source_kind: str | None = None
    size: int | None = None


def content_summary(content: MessageContent) -> ContentSummary:
    if isinstance(content, TextContent):
        return ContentSummary(Modality.TEXT, size=len(content.text))
    if isinstance(content, JsonContent):
        from roboagent.message import canonical_json_dumps

        return ContentSummary(
            Modality.TEXT, size=len(canonical_json_dumps(content.value))
        )
    if isinstance(content, ArtifactReferenceContent):
        return ContentSummary(
            Modality.FILE, content.media_type, "workspace", content.size
        )
    source = content.source
    return ContentSummary(
        modality(content),
        content.media_type,
        type(source).__name__.removesuffix("Source").lower(),
        len(source.data) if isinstance(source, BytesSource) else None,
    )


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class RunState:
    phase: RunPhase
    turn: int
    status: RunStatus | None = None
    streaming_content: tuple[ContentSummary, ...] = ()
    pending_tool_calls: tuple[ToolCallSummary, ...] = ()
    error: RunError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "streaming_content", tuple(self.streaming_content))
        object.__setattr__(self, "pending_tool_calls", tuple(self.pending_tool_calls))
        if (
            not isinstance(self.phase, RunPhase)
            or not isinstance(self.turn, int)
            or isinstance(self.turn, bool)
            or self.turn < 0
        ):
            raise ValueError("RunState requires canonical phase and non-negative turn.")
        if self.status is not None and not isinstance(self.status, RunStatus):
            raise TypeError("RunState.status must be RunStatus or None.")
        if self.error is not None and not isinstance(self.error, RunError):
            raise TypeError("RunState.error must be RunError or None.")


class MediaOwnership(Enum):
    BORROWED = "borrowed"
    OWNED = "owned"


class MediaResolutionErrorCode(Enum):
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FETCH_FAILED = "fetch_failed"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"


class MediaResolutionError(Exception):
    def __init__(self, code: MediaResolutionErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class ResolvedMedia:
    payload: bytes | Path
    media_type: str | None
    size: int
    source: MediaSource
    ownership: MediaOwnership
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, (bytes, Path)):
            raise TypeError("ResolvedMedia.payload must be bytes | Path.")
        if (
            self.size < 0
            or isinstance(self.payload, bytes)
            and self.size != len(self.payload)
        ):
            raise ValueError("ResolvedMedia.size is invalid.")
        _source(self.source)
        _mime(self.media_type)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.ownership is MediaOwnership.OWNED and isinstance(self.payload, Path):
            try:
                self.payload.unlink(missing_ok=True)
            except OSError:
                pass
        elif self.ownership is MediaOwnership.OWNED:
            self.payload = b""


class MediaResolver(Protocol):
    async def resolve(
        self,
        source: MediaSource,
        *,
        expected_media_type: str | None,
        cancellation: CancellationToken,
    ) -> ResolvedMedia: ...
