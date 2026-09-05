"""Canonical immutable JSON, multimodal content, and transcript messages."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import time
from typing import TypeAlias
from urllib.parse import urlparse

_MIME = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")


class ProtocolError(ValueError):
    pass


class UnsupportedContentTypeError(ProtocolError):
    pass


class UnsupportedMediaSourceError(ProtocolError):
    pass


JsonScalar: TypeAlias = str | int | float | bool | None


class FrozenJsonArray(tuple):
    """Ordered immutable JSON array."""

    def __new__(cls, values: Iterable[object] = ()) -> "FrozenJsonArray":
        return tuple.__new__(cls, (freeze_json(value) for value in values))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, (list, tuple)) and len(self) == len(other) and all(
            _json_equal(left, right) for left, right in zip(self, other, strict=True)
        )

    __hash__ = None


class FrozenJsonObject(Mapping[str, object]):
    """Immutable JSON object preserving canonical insertion order."""

    __slots__ = ("_items", "_values")

    def __init__(self, values: Mapping[str, object] | Iterable[tuple[str, object]] = ()) -> None:
        items = values.items() if isinstance(values, Mapping) else values
        built: list[tuple[str, object]] = []
        index: dict[str, object] = {}
        for key, value in items:
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings.")
            if key in index:
                raise ProtocolError(f"Duplicate JSON object key: {key!r}.")
            frozen = freeze_json(value)
            built.append((key, frozen))
            index[key] = frozen
        self._items = tuple(built)
        self._values = index

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({dict(self._items)!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and len(self) == len(other) and all(
            key in other and _json_equal(value, other[key]) for key, value in self._items
        )

    __hash__ = None


JsonValue: TypeAlias = JsonScalar | FrozenJsonArray | FrozenJsonObject
EMPTY_JSON_OBJECT = FrozenJsonObject()


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, FrozenJsonObject):
        return left == right
    if isinstance(left, FrozenJsonArray):
        return left == right
    return left == right


def freeze_json(value: object) -> JsonValue:
    """Validate, deeply copy, and freeze one JSON-compatible value."""
    if value is None or type(value) in {str, bool}:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProtocolError("JSON numbers must be finite.")
        return value
    if isinstance(value, (FrozenJsonObject, FrozenJsonArray)):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(value)
    if isinstance(value, (list, tuple)):
        return FrozenJsonArray(value)
    raise ProtocolError(f"Unsupported JSON value: {type(value).__name__}.")


def freeze_json_object(value: Mapping[str, object] | None = None) -> FrozenJsonObject:
    frozen = freeze_json({} if value is None else value)
    if not isinstance(frozen, FrozenJsonObject):
        raise ProtocolError("Expected a JSON object.")
    return frozen


def thaw_json(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, FrozenJsonArray):
        return [thaw_json(child) for child in value]
    return value


def canonical_json_dumps(value: object) -> str:
    return json.dumps(thaw_json(freeze_json(value)), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class MediaLimits:
    max_inline_bytes: int = 8 * 1024 * 1024
    max_contents_per_message: int = 16

    def __post_init__(self) -> None:
        if self.max_inline_bytes < 1 or self.max_contents_per_message < 1:
            raise ValueError("Media limits must be positive.")


_DEFAULT_MEDIA_LIMITS = MediaLimits()


@dataclass(frozen=True, slots=True)
class BytesSource:
    data: bytes

    def __post_init__(self) -> None:
        if type(self.data) is not bytes or not self.data:
            raise ProtocolError("BytesSource.data must be non-empty bytes.")


@dataclass(frozen=True, slots=True)
class FileSource:
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip() or not Path(self.path).is_absolute():
            raise ProtocolError("FileSource.path must be non-empty and absolute.")


@dataclass(frozen=True, slots=True)
class UrlSource:
    url: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url) if isinstance(self.url, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProtocolError("UrlSource.url must be an absolute http(s) URL.")


MediaSource: TypeAlias = BytesSource | FileSource | UrlSource


def _source(value: object) -> None:
    if not isinstance(value, (BytesSource, FileSource, UrlSource)):
        raise UnsupportedMediaSourceError(f"Unsupported media source: {type(value).__name__}")


def _mime(value: str | None, prefix: str | None = None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value != value.lower() or not _MIME.fullmatch(value):
        raise ProtocolError("media_type must be lower-case, parameter-free MIME.")
    if prefix and not value.startswith(prefix):
        raise ProtocolError(f"media_type must be {prefix}*.")


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ProtocolError("TextContent.text must be str.")


@dataclass(frozen=True, slots=True)
class ImageContent:
    source: MediaSource
    media_type: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        _source(self.source)
        _mime(self.media_type, "image/")
        if self.detail is not None and not isinstance(self.detail, str):
            raise ProtocolError("ImageContent.detail must be str | None.")


@dataclass(frozen=True, slots=True)
class AudioContent:
    source: MediaSource
    media_type: str | None = None
    transcript: str | None = None

    def __post_init__(self) -> None:
        _source(self.source)
        _mime(self.media_type, "audio/")
        if self.transcript is not None and not isinstance(self.transcript, str):
            raise ProtocolError("AudioContent.transcript must be str | None.")


@dataclass(frozen=True, slots=True)
class FileContent:
    source: MediaSource
    media_type: str | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        _source(self.source)
        _mime(self.media_type)
        if self.filename is not None and not isinstance(self.filename, str):
            raise ProtocolError("FileContent.filename must be str | None.")


MessageContent: TypeAlias = TextContent | ImageContent | AudioContent | FileContent


def normalize_content(value: Iterable[MessageContent] | str, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> tuple[MessageContent, ...]:
    if isinstance(value, str):
        value = (TextContent(value),)
    elif isinstance(value, (bytes, bytearray)):
        raise ProtocolError("bytes are not message content.")
    try:
        result = tuple(value)
    except TypeError as exc:
        raise ProtocolError("content must be a sequence of MessageContent.") from exc
    if len(result) > limits.max_contents_per_message:
        raise ProtocolError("Too many content blocks.")
    for item in result:
        if not isinstance(item, (TextContent, ImageContent, AudioContent, FileContent)):
            raise UnsupportedContentTypeError(type(item).__name__)
        source = getattr(item, "source", None)
        if isinstance(source, BytesSource) and len(source.data) > limits.max_inline_bytes:
            raise ProtocolError("Inline media exceeds limit.")
    return result


def _message_timestamp(value: float | None) -> float:
    result = time() if value is None else value
    if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result):
        raise ProtocolError("Message timestamp must be finite.")
    return float(result)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: FrozenJsonObject = field(default_factory=FrozenJsonObject)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.name, str) or not self.name:
            raise ProtocolError("Tool call id and name are required.")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))


class ToolResultStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class UserMessage:
    content: tuple[MessageContent, ...]
    timestamp: float = field(default_factory=time)

    def __init__(self, content: Iterable[MessageContent] | str, *, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS, timestamp: float | None = None) -> None:
        normalized = normalize_content(content, limits)
        if not normalized or (all(isinstance(x, TextContent) for x in normalized) and not any(x.text.strip() for x in normalized)):
            raise ProtocolError("UserMessage requires non-whitespace text or media.")
        object.__setattr__(self, "content", normalized)
        object.__setattr__(self, "timestamp", _message_timestamp(timestamp))

    @property
    def role(self) -> str:
        return "user"


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: tuple[MessageContent, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    timestamp: float = field(default_factory=time)

    def __init__(self, content: Iterable[MessageContent] | str = (), tool_calls: Iterable[ToolCall] = (), *, timestamp: float | None = None, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> None:
        normalized = normalize_content(content, limits)
        calls = tuple(tool_calls)
        if not all(isinstance(call, ToolCall) for call in calls):
            raise ProtocolError("tool_calls must contain ToolCall values.")
        if len({call.id for call in calls}) != len(calls):
            raise ProtocolError("Duplicate ToolCall ID.")
        object.__setattr__(self, "content", normalized)
        object.__setattr__(self, "tool_calls", calls)
        object.__setattr__(self, "timestamp", _message_timestamp(timestamp))

    @property
    def role(self) -> str:
        return "assistant"


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    status: ToolResultStatus
    content: tuple[MessageContent, ...] = ()
    error: object | None = None
    timestamp: float = field(default_factory=time)

    def __init__(self, tool_call_id: str, tool_name: str, status: ToolResultStatus, content: Iterable[MessageContent] | str = (), error: object | None = None, *, timestamp: float | None = None, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> None:
        if not isinstance(tool_call_id, str) or not tool_call_id or not isinstance(tool_name, str) or not tool_name:
            raise ProtocolError("Tool result must identify a call and tool.")
        if not isinstance(status, ToolResultStatus):
            raise ProtocolError("Invalid ToolResult status.")
        if status is ToolResultStatus.SUCCESS and error is not None:
            raise ProtocolError("Successful ToolResult cannot contain an error.")
        if status is ToolResultStatus.ERROR and error is None:
            raise ProtocolError("Failed ToolResult requires error metadata.")
        if error is not None:
            # Keep ToolResultMessage deeply immutable without introducing a
            # module-level message -> tool import cycle.
            from roboagent.tool.tool import ToolErrorInfo

            if not isinstance(error, ToolErrorInfo):
                raise ProtocolError("ToolResult error must be canonical ToolErrorInfo.")
        object.__setattr__(self, "tool_call_id", tool_call_id)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "content", normalize_content(content, limits))
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "timestamp", _message_timestamp(timestamp))

    @property
    def role(self) -> str:
        return "tool"


AgentMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage
ModelMessage: TypeAlias = AgentMessage


class TranscriptValidator:
    def __init__(self, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> None:
        self.limits = limits

    def validate(self, messages: Iterable[AgentMessage], *, complete: bool = True) -> None:
        pending: tuple[ToolCall, ...] = ()
        position = 0
        for message in messages:
            if not isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
                raise ProtocolError("Unknown message type.")
            if normalize_content(message.content, self.limits) != message.content:
                raise ProtocolError("Message content is not canonical.")
            if pending:
                if not isinstance(message, ToolResultMessage):
                    raise ProtocolError("Tool exchange must be contiguous.")
                call = pending[position]
                if message.tool_call_id != call.id or message.tool_name != call.name:
                    raise ProtocolError("Tool results must follow call order.")
                position += 1
                if position == len(pending):
                    pending = ()
                    position = 0
            elif isinstance(message, ToolResultMessage):
                raise ProtocolError("Orphan ToolResultMessage.")
            elif isinstance(message, AssistantMessage) and message.tool_calls:
                pending = message.tool_calls
        if pending and complete:
            raise ProtocolError("Dangling ToolCall exchange.")


def text_of(content: Iterable[MessageContent]) -> str:
    return "".join(part.text for part in content if isinstance(part, TextContent))
