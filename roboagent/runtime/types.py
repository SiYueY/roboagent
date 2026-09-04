"""V1 runtime types.  These deliberately contain no provider-specific API."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from roboagent.message import (
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    ImageContent,
    MediaSource,
    Message,
    MessageContent,
    TextContent,
)
from roboagent.message import _mime, _source

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def _safe_error_code(value: str) -> str:
    if not isinstance(value, str) or not _ERROR_CODE.fullmatch(value):
        raise ValueError("Error code must be a safe identifier.")
    return value


class CancellationReason(Enum):
    USER = "user"
    TIMEOUT = "timeout"
    RUN_TERMINATED = "run_terminated"
    TOOL_POLICY = "tool_policy"


class RuntimeCancellation:
    def __init__(self, parent: "RuntimeCancellation | None" = None) -> None:
        self._event = asyncio.Event()
        self._reason: CancellationReason | None = None
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or bool(self._parent and self._parent.cancelled)

    @property
    def reason(self) -> CancellationReason | None:
        return self._reason or (self._parent.reason if self._parent else None)

    def cancel(self, reason: CancellationReason) -> None:
        if not self.cancelled:
            self._reason = reason
            self._event.set()

    async def wait_cancelled(self) -> None:
        if self.cancelled:
            return
        own = asyncio.create_task(self._event.wait())
        parent = (
            asyncio.create_task(self._parent.wait_cancelled()) if self._parent else None
        )
        done, pending = await asyncio.wait(
            [task for task in (own, parent) if task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(
                self.reason.value if self.reason else "cancelled"
            )

    def child(self) -> "RuntimeCancellation":
        return RuntimeCancellation(self)


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...
    @property
    def reason(self) -> CancellationReason | None: ...
    async def wait_cancelled(self) -> None: ...
    def throw_if_cancelled(self) -> None: ...
    def child(self) -> "CancellationToken": ...


class Modality(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"


def modality(value: MessageContent) -> Modality:
    if isinstance(value, TextContent):
        return Modality.TEXT
    if isinstance(value, ImageContent):
        return Modality.IMAGE
    if isinstance(value, AudioContent):
        return Modality.AUDIO
    return Modality.FILE


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    input_modalities: frozenset[Modality]
    output_modalities: frozenset[Modality]
    tool_result_modalities: frozenset[Modality]
    supports_tools: bool = False

    def __post_init__(self) -> None:
        for name in ("input_modalities", "output_modalities", "tool_result_modalities"):
            values = frozenset(getattr(self, name))
            if not all(isinstance(value, Modality) for value in values):
                raise TypeError(f"{name} must contain Modality values.")
            object.__setattr__(self, name, values)
        if not isinstance(self.supports_tools, bool):
            raise TypeError("supports_tools must be bool.")


class ModelCapabilityError(Exception):
    def __init__(self, code: str) -> None:
        self.code = _safe_error_code(code)
        super().__init__(code)


class ModelProtocolError(Exception):
    """A provider stream could not be normalized into canonical output."""

    def __init__(self, code: str) -> None:
        self.code = _safe_error_code(code)
        super().__init__(code)


class ContextPreparationError(Exception):
    """Tool resolution or context construction failed before model invocation."""


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelContext:
    system_prompt: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]


@dataclass(frozen=True, slots=True)
class RunContext:
    session_id: str
    run_id: str
    cancellation: CancellationToken
    turn: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    context: ModelContext
    run_context: RunContext
    media_resolver: "MediaResolver | None" = None


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("TextDelta.text must be str.")


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or self.index < 0:
            raise ValueError("ToolCallDelta.index must be non-negative int.")
        if self.call_id is not None and (
            not isinstance(self.call_id, str) or not self.call_id
        ):
            raise ValueError("ToolCallDelta.call_id must be non-empty str | None.")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("ToolCallDelta.name must be non-empty str | None.")
        if not isinstance(self.arguments_delta, str):
            raise TypeError("ToolCallDelta.arguments_delta must be str.")


@dataclass(frozen=True, slots=True)
class ContentCompleted:
    content: ImageContent | AudioContent | FileContent

    def __post_init__(self) -> None:
        if not isinstance(self.content, (ImageContent, AudioContent, FileContent)):
            raise TypeError("ContentCompleted requires non-text canonical content.")


ModelStreamItem = TextDelta | ToolCallDelta | ContentCompleted


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    message: AssistantMessage


@dataclass(frozen=True, slots=True)
class ModelFailed:
    error: str

    def __post_init__(self) -> None:
        _safe_error_code(self.error)


ModelEvent = TextDelta | ToolCallDelta | ContentCompleted | ModelCompleted | ModelFailed


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
        if not isinstance(self.size, int) or self.size < 0:
            raise ValueError("ResolvedMedia.size must be a non-negative int.")
        if isinstance(self.payload, bytes) and self.size != len(self.payload):
            raise ValueError("ResolvedMedia.size must match a bytes payload.")
        _source(self.source)
        _mime(self.media_type)
        if not isinstance(self.ownership, MediaOwnership):
            raise TypeError("ResolvedMedia.ownership must be MediaOwnership.")

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
            # Drop the Kernel reference to resolver-created bytes.  Python
            # cannot zero immutable bytes, but this releases the payload.
            self.payload = b""


class MediaResolver(Protocol):
    async def resolve(
        self,
        source: MediaSource,
        *,
        expected_media_type: str | None,
        run_context: RunContext,
        cancellation: CancellationToken,
    ) -> ResolvedMedia: ...


class RunStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MAX_TURNS = "max_turns"


class RunPhase(Enum):
    IDLE = "idle"
    PREPARING_CONTEXT = "preparing_context"
    MODEL = "model"
    TOOL = "tool"
    BETWEEN_TURNS = "between_turns"
    TERMINAL = "terminal"


class RunTerminationReason(Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MAX_TURNS = "max_turns"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    CONTEXT_ERROR = "context_error"
    INVALID_STATE = "invalid_state"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    message: str
    retryable: bool = False
    cause_type: str | None = None

    def __post_init__(self) -> None:
        _safe_error_code(self.code)
        if not isinstance(self.message, str) or not isinstance(self.retryable, bool):
            raise TypeError("Invalid RunError metadata.")
        if self.cause_type is not None and not isinstance(self.cause_type, str):
            raise TypeError("RunError.cause_type must be str | None.")


@dataclass(frozen=True, slots=True)
class ContentSummary:
    modality: Modality
    media_type: str | None = None
    source_kind: str | None = None
    size: int | None = None


def content_summary(content: MessageContent) -> ContentSummary:
    """Create the media-safe representation used by events and run state."""
    if isinstance(content, TextContent):
        return ContentSummary(Modality.TEXT, size=len(content.text))
    source = content.source
    return ContentSummary(
        modality(content),
        content.media_type,
        type(source).__name__.removesuffix("Source").lower(),
        len(source.data) if isinstance(source, BytesSource) else None,
    )


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """Public scheduling metadata; raw arguments remain canonical-transcript only."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class RunState:
    status: RunStatus
    phase: RunPhase
    turn: int
    streaming_content: tuple[ContentSummary, ...] = ()
    pending_tool_calls: tuple[ToolCallSummary, ...] = ()
    error: RunError | None = None
