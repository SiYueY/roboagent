"""Canonical Session snapshots and CAS repositories."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from roboagent.context import ContextSummary
from roboagent.message import (
    AgentMessage,
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    FileSource,
    FrozenJsonArray,
    FrozenJsonObject,
    ImageContent,
    JsonContent,
    JsonValue,
    MediaSource,
    MessageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UrlSource,
    UserMessage,
    freeze_json,
)
from roboagent.tool import ToolErrorInfo

from .session import InputReceipt, PendingInput


SCHEMA_VERSION = 1


class SessionPersistenceError(RuntimeError):
    code = "session_persistence_error"


class SessionConflictError(SessionPersistenceError):
    code = "session_conflict"


class SessionCorruptedError(SessionPersistenceError):
    code = "session_corrupted"


class SessionVersionUnsupportedError(SessionPersistenceError):
    code = "session_version_unsupported"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    schema_version: int
    session_id: str
    revision: int
    last_pending_sequence: int
    messages: tuple[AgentMessage, ...]
    pending: tuple[PendingInput, ...]
    compaction: ContextSummary | None = None
    metadata: FrozenJsonObject = field(default_factory=FrozenJsonObject)

    def __post_init__(self) -> None:
        integer_fields = (self.schema_version, self.revision, self.last_pending_sequence)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in integer_fields):
            raise TypeError("SessionSnapshot versions and revisions must be integers.")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "pending", tuple(self.pending))
        object.__setattr__(self, "metadata", FrozenJsonObject(self.metadata))
        if self.schema_version < 1 or self.revision < 0 or self.last_pending_sequence < 0:
            raise ValueError("Invalid SessionSnapshot version or revision.")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("SessionSnapshot.session_id must be non-empty.")
        if not all(isinstance(message, AgentMessage) for message in self.messages):
            raise TypeError("SessionSnapshot.messages must contain AgentMessage values.")
        if not all(isinstance(item, PendingInput) for item in self.pending):
            raise TypeError("SessionSnapshot.pending must contain PendingInput values.")
        if self.compaction is not None and not isinstance(self.compaction, ContextSummary):
            raise TypeError("SessionSnapshot.compaction must be ContextSummary or None.")


class SessionSnapshotCodec(Protocol):
    def encode(self, snapshot: SessionSnapshot) -> bytes: ...
    def decode(self, data: bytes) -> SessionSnapshot: ...


class CanonicalMessageCodec:
    def __init__(self, *, max_inline_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_inline_bytes = max_inline_bytes

    def encode(self, message: AgentMessage) -> dict[str, object]:
        data: dict[str, object] = {
            "type": f"{message.role}_message",
            "timestamp": message.timestamp,
            "content": [self._content(item) for item in message.content],
        }
        if isinstance(message, AssistantMessage):
            data["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": _encode_json(call.arguments)}
                for call in message.tool_calls
            ]
        elif isinstance(message, ToolResultMessage):
            data.update(
                {
                    "tool_call_id": message.tool_call_id,
                    "tool_name": message.tool_name,
                    "status": message.status.value,
                    "error": None
                    if message.error is None
                    else _error_data(cast(ToolErrorInfo, message.error)),
                }
            )
        return data

    def decode(self, data: object) -> AgentMessage:
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise SessionCorruptedError("Invalid message record.")
        timestamp = data.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise SessionCorruptedError("Invalid message timestamp.")
        content: tuple[MessageContent, ...] = tuple(
            self._decode_content(item) for item in data["content"]
        )
        kind = data.get("type")
        try:
            if kind == "user_message":
                return UserMessage(content, timestamp=float(timestamp))
            if kind == "assistant_message":
                calls = tuple(self._decode_tool_call(item) for item in _list(data.get("tool_calls", [])))
                return AssistantMessage(content, calls, timestamp=float(timestamp))
            if kind == "tool_message":
                status = ToolResultStatus(data["status"])
                error_data = data.get("error")
                error = None if error_data is None else ToolErrorInfo(**_dict(error_data))
                return ToolResultMessage(
                    data["tool_call_id"], data["tool_name"], status, content, error, timestamp=float(timestamp)
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionCorruptedError("Invalid message fields.") from exc
        raise SessionCorruptedError("Unknown message type.")

    def _decode_tool_call(self, data: object) -> ToolCall:
        value = _dict(data)
        arguments = _decode_json(value["arguments"])
        if not isinstance(arguments, FrozenJsonObject):
            raise SessionCorruptedError("ToolCall arguments must be a JSON object.")
        return ToolCall(value["id"], value["name"], arguments)

    def _content(self, item: object) -> dict[str, object]:
        if isinstance(item, TextContent):
            return {"type": "text", "text": item.text}
        if isinstance(item, JsonContent):
            return {"type": "json", "value": _encode_json(item.value)}
        if isinstance(item, ArtifactReferenceContent):
            return {
                "type": "artifact_reference", "uri": item.uri, "media_type": item.media_type,
                "size": item.size, "digest": item.digest, "preview": item.preview,
            }
        if isinstance(item, (ImageContent, AudioContent, FileContent)):
            source = item.source
            if isinstance(source, BytesSource):
                if len(source.data) > self.max_inline_bytes:
                    raise SessionPersistenceError("Inline media exceeds snapshot limit.")
                source_data = {"type": "bytes", "encoding": "base64", "data": base64.b64encode(source.data).decode("ascii")}
            elif isinstance(source, FileSource):
                source_data = {"type": "file", "path": source.path}
            else:
                source_data = {"type": "url", "url": source.url}
            data: dict[str, object] = {"type": "image" if isinstance(item, ImageContent) else "audio" if isinstance(item, AudioContent) else "file", "source": source_data, "media_type": item.media_type}
            data["detail" if isinstance(item, ImageContent) else "transcript" if isinstance(item, AudioContent) else "filename"] = getattr(item, "detail", getattr(item, "transcript", getattr(item, "filename", None)))
            return data
        raise SessionPersistenceError("Unknown message content type.")

    def _decode_content(self, data: object) -> MessageContent:
        value = _dict(data)
        kind = value.get("type")
        try:
            if kind == "text":
                return TextContent(value["text"])
            if kind == "json":
                return JsonContent(_decode_json(value["value"]))
            if kind == "artifact_reference":
                return ArtifactReferenceContent(value["uri"], value.get("media_type"), value["size"], value["digest"], value.get("preview"))
            if kind in {"image", "audio", "file"}:
                source_data = _dict(value["source"])
                source_kind = source_data.get("type")
                source: MediaSource
                if source_kind == "bytes":
                    if source_data.get("encoding") != "base64":
                        raise SessionCorruptedError("Unknown bytes encoding.")
                    payload = base64.b64decode(source_data["data"], validate=True)
                    if len(payload) > self.max_inline_bytes:
                        raise SessionCorruptedError("Inline media exceeds snapshot limit.")
                    source = BytesSource(payload)
                elif source_kind == "file":
                    source = FileSource(source_data["path"])
                elif source_kind == "url":
                    source = UrlSource(source_data["url"])
                else:
                    raise SessionCorruptedError("Unknown media source type.")
                if kind == "image":
                    return ImageContent(source, value.get("media_type"), value.get("detail"))
                if kind == "audio":
                    return AudioContent(source, value.get("media_type"), value.get("transcript"))
                return FileContent(source, value.get("media_type"), value.get("filename"))
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionCorruptedError("Invalid content fields.") from exc
        raise SessionCorruptedError("Unknown content type.")


class JsonSessionSnapshotCodec:
    def __init__(self, *, max_inline_bytes: int = 8 * 1024 * 1024) -> None:
        self.messages = CanonicalMessageCodec(max_inline_bytes=max_inline_bytes)

    def encode(self, snapshot: SessionSnapshot) -> bytes:
        data = {
            "schema_version": snapshot.schema_version,
            "session_id": snapshot.session_id,
            "revision": snapshot.revision,
            "last_pending_sequence": snapshot.last_pending_sequence,
            "messages": [self.messages.encode(item) for item in snapshot.messages],
            "pending": [
                {
                    "receipt": {"input_id": item.receipt.input_id, "sequence": item.receipt.sequence, "session_id": item.receipt.session_id},
                    "message": self.messages.encode(item.message),
                    "kind": item.kind,
                }
                for item in snapshot.pending
            ],
            "compaction": None if snapshot.compaction is None else {
                "source_start": snapshot.compaction.source_start,
                "source_end_exclusive": snapshot.compaction.source_end_exclusive,
                "source_digest": snapshot.compaction.source_digest,
                "text": snapshot.compaction.text,
                "summary_format_version": snapshot.compaction.summary_format_version,
                "summarizer_id": snapshot.compaction.summarizer_id,
            },
            "metadata": _encode_json(snapshot.metadata),
        }
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def decode(self, data: bytes) -> SessionSnapshot:
        try:
            raw = json.loads(data)
        except Exception as exc:
            raise SessionCorruptedError("Snapshot is not valid JSON.") from exc
        value = _dict(raw)
        if value.get("schema_version") != SCHEMA_VERSION:
            raise SessionVersionUnsupportedError("Unsupported Session snapshot version.")
        try:
            messages = tuple(self.messages.decode(item) for item in _list(value["messages"]))
            pending = tuple(self._decode_pending(item) for item in _list(value["pending"]))
            compaction_data = value.get("compaction")
            compaction = None if compaction_data is None else ContextSummary(**_dict(compaction_data))
            metadata = _decode_json(value["metadata"])
            if not isinstance(metadata, FrozenJsonObject):
                raise SessionCorruptedError("Session metadata must be a JSON object.")
            return SessionSnapshot(
                value["schema_version"], value["session_id"], value["revision"], value["last_pending_sequence"],
                messages, pending, compaction, metadata,
            )
        except SessionPersistenceError:
            raise
        except Exception as exc:
            raise SessionCorruptedError("Invalid Session snapshot.") from exc

    def _decode_pending(self, data: object) -> PendingInput:
        value = _dict(data)
        message = self.messages.decode(value["message"])
        if not isinstance(message, UserMessage):
            raise SessionCorruptedError("Pending messages must be UserMessage values.")
        return PendingInput(InputReceipt(**_dict(value["receipt"])), message, value["kind"])


class SessionRepository(Protocol):
    async def load(self, session_id: str) -> SessionSnapshot | None: ...
    async def save(self, snapshot: SessionSnapshot, *, expected_revision: int | None) -> int: ...
    async def delete(self, session_id: str, *, expected_revision: int) -> None: ...


class InMemorySessionRepository:
    def __init__(self, codec: SessionSnapshotCodec | None = None) -> None:
        self.codec = codec or JsonSessionSnapshotCodec()
        self._records: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> SessionSnapshot | None:
        async with self._lock:
            data = self._records.get(session_id)
        if data is None:
            return None
        snapshot = self.codec.decode(data)
        _check_loaded_session_id(snapshot, session_id)
        return snapshot

    async def save(self, snapshot: SessionSnapshot, *, expected_revision: int | None) -> int:
        data = self.codec.encode(snapshot)
        async with self._lock:
            current_data = self._records.get(snapshot.session_id)
            current = None if current_data is None else self.codec.decode(current_data).revision
            _check_cas(current, expected_revision, snapshot.revision)
            self._records[snapshot.session_id] = data
        return snapshot.revision

    async def delete(self, session_id: str, *, expected_revision: int) -> None:
        async with self._lock:
            data = self._records.get(session_id)
            current = None if data is None else self.codec.decode(data).revision
            if current != expected_revision:
                raise SessionConflictError("Stale Session delete.")
            del self._records[session_id]


class LocalSessionRepository:
    def __init__(self, root: Path | str, codec: SessionSnapshotCodec | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.codec = codec or JsonSessionSnapshotCodec()

    async def load(self, session_id: str) -> SessionSnapshot | None:
        return await asyncio.to_thread(self._load, session_id)

    async def save(self, snapshot: SessionSnapshot, *, expected_revision: int | None) -> int:
        return await asyncio.to_thread(self._save, snapshot, expected_revision)

    async def delete(self, session_id: str, *, expected_revision: int) -> None:
        await asyncio.to_thread(self._delete, session_id, expected_revision)

    def _paths(self, session_id: str) -> tuple[Path, Path]:
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", session_id):
            raise SessionPersistenceError("Invalid session ID for local repository.")
        return self.root / f"{session_id}.json", self.root / f"{session_id}.lock"

    def _load(self, session_id: str) -> SessionSnapshot | None:
        target, lock_path = self._paths(session_id)
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                if not target.exists():
                    return None
                snapshot = self.codec.decode(target.read_bytes())
                _check_loaded_session_id(snapshot, session_id)
                return snapshot
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, snapshot: SessionSnapshot, expected_revision: int | None) -> int:
        target, lock_path = self._paths(snapshot.session_id)
        encoded = self.codec.encode(snapshot)
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                current = None if not target.exists() else self.codec.decode(target.read_bytes()).revision
                _check_cas(current, expected_revision, snapshot.revision)
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{snapshot.session_id}.{os.getpid()}.", suffix=".tmp", dir=self.root
                )
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, target)
                    _fsync_directory(self.root)
                finally:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
                return snapshot.revision
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _delete(self, session_id: str, expected_revision: int) -> None:
        target, lock_path = self._paths(session_id)
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                current = None if not target.exists() else self.codec.decode(target.read_bytes()).revision
                if current != expected_revision:
                    raise SessionConflictError("Stale Session delete.")
                target.unlink()
                _fsync_directory(self.root)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _check_cas(current: int | None, expected: int | None, new: int) -> None:
    if current != expected or (current is not None and new <= current):
        raise SessionConflictError("Stale Session writer.")


def _check_loaded_session_id(snapshot: SessionSnapshot, requested: str) -> None:
    if snapshot.session_id != requested:
        raise SessionCorruptedError("Snapshot session ID does not match its repository key.")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _encode_json(value: object) -> object:
    frozen = freeze_json(value)
    if isinstance(frozen, FrozenJsonObject):
        return {"type": "object", "entries": [[key, _encode_json(item)] for key, item in frozen.items()]}
    if isinstance(frozen, FrozenJsonArray):
        return {"type": "array", "items": [_encode_json(item) for item in frozen]}
    return {"type": "scalar", "value": frozen}


def _decode_json(value: object) -> JsonValue:
    data = _dict(value)
    kind = data.get("type")
    if kind == "scalar":
        if "value" not in data:
            raise SessionCorruptedError("Scalar JSON value is missing its value field.")
        return freeze_json(data["value"])
    if kind == "array":
        return FrozenJsonArray(_decode_json(item) for item in _list(data.get("items")))
    if kind == "object":
        entries = _list(data.get("entries"))
        if not all(isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str) for entry in entries):
            raise SessionCorruptedError("Invalid ordered JSON object entries.")
        return FrozenJsonObject((entry[0], _decode_json(entry[1])) for entry in entries)
    raise SessionCorruptedError("Unknown JSON value type.")


def _error_data(error: ToolErrorInfo) -> dict[str, object]:
    return {"code": error.code, "message": error.message, "retryable": error.retryable}


def _dict(value: object) -> dict:
    if not isinstance(value, dict):
        raise SessionCorruptedError("Expected JSON object.")
    return value


def _list(value: object) -> list:
    if not isinstance(value, list):
        raise SessionCorruptedError("Expected JSON array.")
    return value
