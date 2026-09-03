"""RoboAgent v1 canonical, modality-neutral transcript protocol."""
from __future__ import annotations

import re
import math
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import time
from typing import Any, Iterable, Mapping, TypeAlias
from types import MappingProxyType
from urllib.parse import urlparse

_MIME = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")

class ProtocolError(ValueError): pass
class UnsupportedContentTypeError(ProtocolError): pass
class UnsupportedMediaSourceError(ProtocolError): pass

@dataclass(frozen=True, slots=True)
class MediaLimits:
    max_inline_bytes: int = 8 * 1024 * 1024
    max_contents_per_message: int = 16
    def __post_init__(self) -> None:
        if self.max_inline_bytes < 1 or self.max_contents_per_message < 1: raise ValueError("Media limits must be positive.")

_DEFAULT_MEDIA_LIMITS = MediaLimits()

@dataclass(frozen=True, slots=True)
class BytesSource:
    data: bytes
    def __post_init__(self) -> None:
        if type(self.data) is not bytes or not self.data: raise ProtocolError("BytesSource.data must be non-empty bytes.")
@dataclass(frozen=True, slots=True)
class FileSource:
    path: str
    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip() or not Path(self.path).is_absolute(): raise ProtocolError("FileSource.path must be non-empty and absolute.")
@dataclass(frozen=True, slots=True)
class UrlSource:
    url: str
    def __post_init__(self) -> None:
        parsed = urlparse(self.url) if isinstance(self.url, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname: raise ProtocolError("UrlSource.url must be an absolute http(s) URL.")
MediaSource: TypeAlias = BytesSource | FileSource | UrlSource

def _source(value: object) -> None:
    if not isinstance(value, (BytesSource, FileSource, UrlSource)): raise UnsupportedMediaSourceError(f"Unsupported media source: {type(value).__name__}")
def _mime(value: str | None, prefix: str | None = None) -> None:
    if value is None: return
    if not isinstance(value, str) or value != value.lower() or not _MIME.fullmatch(value): raise ProtocolError("media_type must be lower-case, parameter-free MIME.")
    if prefix and not value.startswith(prefix): raise ProtocolError(f"media_type must be {prefix}*.")

@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
    def __post_init__(self) -> None:
        if not isinstance(self.text, str): raise ProtocolError("TextContent.text must be str.")
@dataclass(frozen=True, slots=True)
class ImageContent:
    source: MediaSource; media_type: str | None = None; detail: str | None = None
    def __post_init__(self) -> None:
        _source(self.source); _mime(self.media_type, "image/")
        if self.detail is not None and not isinstance(self.detail, str): raise ProtocolError("ImageContent.detail must be str | None.")
@dataclass(frozen=True, slots=True)
class AudioContent:
    source: MediaSource; media_type: str | None = None; transcript: str | None = None
    def __post_init__(self) -> None:
        _source(self.source); _mime(self.media_type, "audio/")
        if self.transcript is not None and not isinstance(self.transcript, str): raise ProtocolError("AudioContent.transcript must be str | None.")
@dataclass(frozen=True, slots=True)
class FileContent:
    source: MediaSource; media_type: str | None = None; filename: str | None = None
    def __post_init__(self) -> None:
        _source(self.source); _mime(self.media_type)
        if self.filename is not None and not isinstance(self.filename, str): raise ProtocolError("FileContent.filename must be str | None.")
MessageContent: TypeAlias = TextContent | ImageContent | AudioContent | FileContent

def normalize_content(value: Iterable[MessageContent] | str, limits: MediaLimits) -> tuple[MessageContent, ...]:
    if isinstance(value, str): value = (TextContent(value),)
    elif isinstance(value, (bytes, bytearray)): raise ProtocolError("bytes are not a MessageContent sequence.")
    try: result = tuple(value)
    except TypeError as exc: raise ProtocolError("content must be a sequence of MessageContent.") from exc
    if len(result) > limits.max_contents_per_message: raise ProtocolError("Too many content blocks.")
    for item in result:
        if not isinstance(item, (TextContent, ImageContent, AudioContent, FileContent)): raise UnsupportedContentTypeError(type(item).__name__)
        if isinstance(getattr(item, "source", None), BytesSource) and len(item.source.data) > limits.max_inline_bytes: raise ProtocolError("Inline media exceeds limit.")
    return result


def freeze_json(value: Any) -> Any:
    """Validate JSON-compatible values and remove mutable containers."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ProtocolError("Tool arguments cannot contain non-finite floats.")
        return value
    if isinstance(value, MappingABC):
        frozen: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProtocolError("Tool arguments object keys must be str.")
            frozen[key] = freeze_json(child)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(child) for child in value)
    raise ProtocolError("Tool arguments must contain JSON-compatible values.")

@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str; name: str; raw_arguments: str; arguments: Mapping[str, Any] | None = None; parse_error: str | None = None
    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.name, str) or not self.name: raise ProtocolError("Tool call id and name are required.")
        if not isinstance(self.raw_arguments, str): raise ProtocolError("Tool call raw_arguments must be str.")
        if self.arguments is not None:
            if not isinstance(self.arguments, MappingABC): raise ProtocolError("Tool call arguments must be object | None.")
            object.__setattr__(self, "arguments", freeze_json(self.arguments))
class ToolCallStatus(Enum): COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"; SKIPPED="skipped"
@dataclass(frozen=True, slots=True)
class ToolExecutionError:
    code: str; message: str; retryable: bool = False
    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _ERROR_CODE.fullmatch(self.code):
            raise ProtocolError("ToolExecutionError.code must be a safe error identifier.")
        if not isinstance(self.message, str) or not isinstance(self.retryable, bool):
            raise ProtocolError("Invalid ToolExecutionError metadata.")

@dataclass(frozen=True, slots=True)
class UserMessage:
    content: tuple[MessageContent, ...]; timestamp: float = field(default_factory=time)
    def __init__(self, content: Iterable[MessageContent] | str, *, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS, timestamp: float | None = None) -> None:
        normalized = normalize_content(content, limits)
        if not normalized or (all(isinstance(x, TextContent) for x in normalized) and not any(x.text.strip() for x in normalized)): raise ProtocolError("UserMessage requires non-whitespace text or media.")
        object.__setattr__(self, "content", normalized); object.__setattr__(self, "timestamp", time() if timestamp is None else timestamp)
    @property
    def role(self) -> str: return "user"
@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: tuple[MessageContent, ...] = (); tool_calls: tuple[ToolCall, ...] = (); finish_reason: str = "stop"; model: str | None = None; timestamp: float = field(default_factory=time); usage: Mapping[str, Any] | None = None
    def __init__(self, content: Iterable[MessageContent] | str = (), tool_calls: Iterable[ToolCall] = (), finish_reason: str = "stop", model: str | None = None, timestamp: float | None = None, *, usage: Mapping[str, Any] | None = None, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> None:
        normalized = normalize_content(content, limits)
        try: calls = tuple(tool_calls)
        except TypeError as exc: raise ProtocolError("tool_calls must be a sequence of ToolCall.") from exc
        if not all(isinstance(call, ToolCall) for call in calls): raise ProtocolError("tool_calls must contain ToolCall.")
        if len({call.id for call in calls}) != len(calls): raise ProtocolError("Duplicate ToolCall ID.")
        if not isinstance(finish_reason, str) or model is not None and not isinstance(model, str): raise ProtocolError("Invalid AssistantMessage metadata.")
        if usage is not None and not isinstance(usage, MappingABC):
            raise ProtocolError("AssistantMessage.usage must be an object | None.")
        object.__setattr__(self, "content", normalized); object.__setattr__(self, "tool_calls", calls)
        object.__setattr__(self, "finish_reason", finish_reason); object.__setattr__(self, "model", model)
        object.__setattr__(self, "timestamp", time() if timestamp is None else timestamp)
        object.__setattr__(self, "usage", freeze_json(usage) if usage is not None else None)
    @property
    def role(self) -> str: return "assistant"
@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str; tool_name: str; status: ToolCallStatus; content: tuple[MessageContent, ...] = (); error: ToolExecutionError | None = None; timestamp: float = field(default_factory=time)
    def __init__(self, tool_call_id: str, tool_name: str, status: ToolCallStatus, content: Iterable[MessageContent] | str = (), error: ToolExecutionError | None = None, timestamp: float | None = None, *, limits: MediaLimits = _DEFAULT_MEDIA_LIMITS) -> None:
        if not isinstance(tool_call_id, str) or not tool_call_id or not isinstance(tool_name, str) or not tool_name: raise ProtocolError("Tool result must identify a call and tool.")
        if not isinstance(status, ToolCallStatus): raise ProtocolError("Tool result has invalid status.")
        if error is not None and not isinstance(error, ToolExecutionError): raise ProtocolError("Tool result error must be ToolExecutionError | None.")
        if status is ToolCallStatus.COMPLETED and error is not None: raise ProtocolError("Completed result cannot have error.")
        object.__setattr__(self, "tool_call_id", tool_call_id); object.__setattr__(self, "tool_name", tool_name); object.__setattr__(self, "status", status)
        object.__setattr__(self, "content", normalize_content(content, limits)); object.__setattr__(self, "error", error)
        object.__setattr__(self, "timestamp", time() if timestamp is None else timestamp)
    @property
    def role(self) -> str: return "tool"
Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage

class TranscriptValidator:
    def __init__(self, limits: MediaLimits) -> None: self.limits = limits
    def validate(self, messages: Iterable[Message], *, complete: bool = True) -> None:
        pending: tuple[ToolCall, ...] = (); position = 0
        for message in messages:
            if not isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)): raise ProtocolError("Unknown message type.")
            if normalize_content(message.content, self.limits) != message.content: raise ProtocolError("Canonical content must already be normalized.")
            if pending:
                if not isinstance(message, ToolResultMessage): raise ProtocolError("Tool exchange must be contiguous.")
                call = pending[position]
                if message.tool_call_id != call.id or message.tool_name != call.name: raise ProtocolError("Tool results must follow call order.")
                position += 1
                if position == len(pending): pending = (); position = 0
            elif isinstance(message, ToolResultMessage): raise ProtocolError("Orphan tool result.")
            elif isinstance(message, AssistantMessage) and message.tool_calls: pending = message.tool_calls
        if pending and complete: raise ProtocolError("Dangling tool calls.")

def text_of(content: Iterable[MessageContent]) -> str: return "".join(part.text for part in content if isinstance(part, TextContent))
