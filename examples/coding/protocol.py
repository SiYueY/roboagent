"""Strict coding parser, value unions, and worker wire protocol."""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import asdict, dataclass
from typing import Literal

WORKER_PROTOCOL = "roboagent.coding.worker/v1"
EXECUTE_PROTOCOL = "roboagent.coding.execute_python/v1"


class CodingProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RoboAgentToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ArtifactHandle:
    uri: str
    media_type: str | None
    size: int
    digest: str
    preview: str | None

    def __post_init__(self) -> None:
        if (
            not self.uri
            or not self.digest
            or type(self.size) is not int
            or self.size < 0
        ):
            raise ValueError("Invalid ArtifactHandle.")
        try:
            from roboagent.message import ArtifactReferenceContent

            ArtifactReferenceContent(
                self.uri, self.media_type, self.size, self.digest, self.preview
            )
        except Exception as exc:
            raise ValueError(
                "ArtifactHandle must contain a canonical artifact reference."
            ) from exc


@dataclass(frozen=True, slots=True)
class PythonFenceResult:
    kind: Literal["text", "python"]
    text: str
    code: str | None = None


_OPEN = re.compile(r"^( {0,3})(`{3,})([^`]*)$")


def parse_python_fence(text: str) -> PythonFenceResult:
    """Parse exactly one strict Python fence while preserving outside text/code."""
    if not isinstance(text, str):
        raise TypeError("text must be str.")
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        logical = lines[index].rstrip("\r\n")
        match = _OPEN.fullmatch(logical)
        if match is None:
            index += 1
            continue
        marker, info = match.group(2), match.group(3)
        stripped = info.strip()
        declared_python = stripped.lower() == "python" or stripped.lower().startswith(
            ("python ", "python\t")
        )
        valid_python = stripped.lower() == "python"
        if (
            declared_python
            and not valid_python
            and not stripped.lower().startswith("python3")
        ):
            raise CodingProtocolError(
                "malformed_python_block", "Malformed Python fence opening."
            )
        start = index
        index += 1
        code_start = index
        closed = False
        while index < len(lines):
            close_line = lines[index].rstrip("\r\n")
            close = re.fullmatch(r" {0,3}(`{%d,})[ \t]*" % len(marker), close_line)
            if close is not None:
                closed = True
                break
            index += 1
        if valid_python:
            if not closed:
                raise CodingProtocolError(
                    "malformed_python_block", "Python fence is not closed."
                )
            code = "".join(lines[code_start:index])
            if not code.strip():
                raise CodingProtocolError(
                    "empty_python_block", "Python block is empty."
                )
            blocks.append((start, index, code))
        index = index + 1 if closed else index
    if not blocks:
        return PythonFenceResult("text", text)
    if len(blocks) != 1:
        raise CodingProtocolError(
            "multiple_python_blocks", "Only one Python block is allowed."
        )
    start, end, code = blocks[0]
    outside = "".join((*lines[:start], *lines[end + 1 :]))
    return PythonFenceResult("python", outside, code)


def final_value(value: object) -> dict[str, object]:
    if value is None:
        return {"kind": "empty", "value": None}
    if isinstance(value, ArtifactHandle):
        return {"kind": "artifact", "value": asdict(value)}
    if isinstance(value, str):
        return {"kind": "text", "value": value}
    if isinstance(value, bool) or type(value) is int:
        return {"kind": "json", "value": value}
    if type(value) is float:
        if not math.isfinite(value):
            raise CodingProtocolError(
                "final_answer_not_serializable", "Final float must be finite."
            )
        return {"kind": "json", "value": value}
    if isinstance(value, (list, dict)):
        try:
            encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise CodingProtocolError(
                "final_answer_not_serializable", "Final value is not JSON-compatible."
            ) from exc
        return {"kind": "json", "value": decoded}
    raise CodingProtocolError(
        "final_answer_not_serializable", "Unsupported final value type."
    )


def validate_final_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise CodingProtocolError(
            "invalid_final_envelope", "Invalid final value envelope."
        )
    kind = value.get("kind")
    raw = value.get("value")
    if kind == "empty" and raw is None:
        return value
    if kind == "text" and isinstance(raw, str):
        return value
    if kind == "artifact" and isinstance(raw, dict):
        try:
            ArtifactHandle(**raw)
        except (TypeError, ValueError) as exc:
            raise CodingProtocolError(
                "invalid_final_envelope", "Invalid artifact final value."
            ) from exc
        return value
    if kind == "json":
        if final_value(raw).get("kind") == "json":
            return value
        raise CodingProtocolError(
            "invalid_final_envelope", "Final kind and value do not match."
        )
    raise CodingProtocolError(
        "invalid_final_envelope", "Final kind and value do not match."
    )


@dataclass(frozen=True, slots=True)
class IpcEnvelope:
    worker_generation: int
    execution_id: str
    request_id: str
    type: str
    payload: dict[str, object]
    protocol: str = WORKER_PROTOCOL

    def __post_init__(self) -> None:
        if (
            self.protocol != WORKER_PROTOCOL
            or type(self.worker_generation) is not int
            or self.worker_generation < 1
        ):
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid worker protocol or generation."
            )
        if not isinstance(self.execution_id, str) or not isinstance(
            self.request_id, str
        ):
            raise CodingProtocolError(
                "ipc_protocol_error", "IPC identities must be strings."
            )
        if not isinstance(self.type, str) or not isinstance(self.payload, dict):
            raise CodingProtocolError("ipc_protocol_error", "Invalid IPC message.")
        empty = {"hello", "accepted", "ready", "shutdown"}
        active = {
            "execute",
            "tool_request",
            "tool_response",
            "execution_result",
            "cancel",
        }
        if self.type not in empty | active:
            raise CodingProtocolError("ipc_protocol_error", "Unknown IPC message type.")
        if self.type in empty and (self.execution_id or self.request_id):
            raise CodingProtocolError(
                "ipc_protocol_error", "Control message identities must be empty."
            )
        if self.type in active and (not self.execution_id or not self.request_id):
            raise CodingProtocolError(
                "ipc_protocol_error", "Execution message identities are required."
            )
        _validate_payload(self.type, self.payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "worker_generation": self.worker_generation,
            "execution_id": self.execution_id,
            "request_id": self.request_id,
            "type": self.type,
            "payload": self.payload,
        }


def encode_frame(envelope: IpcEnvelope, max_bytes: int = 1024 * 1024) -> bytes:
    body = json.dumps(
        envelope.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(body) > max_bytes:
        raise CodingProtocolError("ipc_protocol_error", "IPC frame exceeds its limit.")
    return struct.pack(">I", len(body)) + body


def decode_frame(frame: bytes, max_bytes: int = 1024 * 1024) -> IpcEnvelope:
    if len(frame) < 4:
        raise CodingProtocolError("ipc_protocol_error", "Partial IPC frame.")
    length = struct.unpack(">I", frame[:4])[0]
    if length > max_bytes or len(frame) != length + 4:
        raise CodingProtocolError("ipc_protocol_error", "Invalid IPC frame length.")
    try:
        data = json.loads(frame[4:].decode("utf-8"))
        if not isinstance(data, dict):
            raise TypeError
        required = {
            "protocol",
            "worker_generation",
            "execution_id",
            "request_id",
            "type",
            "payload",
        }
        if not required <= data.keys():
            raise TypeError
        return IpcEnvelope(**{key: data[key] for key in required})
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise CodingProtocolError("ipc_protocol_error", "Malformed IPC frame.") from exc


def validate_direction(
    envelope: IpcEnvelope, source: Literal["host", "worker"]
) -> None:
    allowed = {
        "host": {"accepted", "execute", "tool_response", "cancel", "shutdown"},
        "worker": {"hello", "ready", "tool_request", "execution_result"},
    }[source]
    if envelope.type not in allowed:
        raise CodingProtocolError(
            "ipc_protocol_error", "IPC message direction is invalid."
        )


def validate_tool_value(
    value: object, *, allow_tuple: bool = True
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise CodingProtocolError("ipc_protocol_error", "Invalid ToolValue envelope.")
    kind, raw = value.get("kind"), value.get("value")
    if kind == "text" and isinstance(raw, str):
        return value
    if kind == "json":
        try:
            json.dumps(raw, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid JSON ToolValue."
            ) from exc
        return value
    if kind == "artifact" and isinstance(raw, dict):
        try:
            ArtifactHandle(**raw)
        except (TypeError, ValueError) as exc:
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid artifact ToolValue."
            ) from exc
        return value
    if kind == "tuple" and allow_tuple and isinstance(raw, list):
        for item in raw:
            validate_tool_value(item, allow_tuple=False)
        return value
    raise CodingProtocolError("ipc_protocol_error", "Invalid ToolValue kind.")


def _validate_payload(type_: str, payload: dict[str, object]) -> None:
    if type_ in {"accepted", "cancel", "shutdown"} and payload:
        raise CodingProtocolError(
            "ipc_protocol_error", "Control payload must be empty."
        )
    if type_ == "hello":
        if (
            set(payload) - {"pid"}
            or "pid" in payload
            and type(payload["pid"]) is not int
        ):
            raise CodingProtocolError("ipc_protocol_error", "Invalid hello payload.")
    elif type_ == "ready":
        if type(payload.get("interpreter_generation")) is not int:
            raise CodingProtocolError("ipc_protocol_error", "Invalid ready payload.")
    elif type_ == "execute":
        tool_names = payload.get("tool_names")
        if not isinstance(payload.get("code"), str) or not isinstance(tool_names, list):
            raise CodingProtocolError("ipc_protocol_error", "Invalid execute payload.")
        if not all(isinstance(item, str) for item in tool_names):
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid execute Tool names."
            )
    elif type_ == "tool_request":
        if not isinstance(payload.get("tool_name"), str) or not isinstance(
            payload.get("arguments"), dict
        ):
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid ToolRequest payload."
            )
    elif type_ == "tool_response":
        if payload.get("ok") is True:
            validate_tool_value(payload.get("value"))
        elif payload.get("ok") is False:
            error = payload.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
                or not isinstance(error.get("retryable"), bool)
            ):
                raise CodingProtocolError(
                    "ipc_protocol_error", "Invalid ToolResponse error."
                )
        else:
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid ToolResponse payload."
            )
    elif type_ == "execution_result":
        if payload.get("execution_status") not in {"ok", "error"}:
            raise CodingProtocolError("ipc_protocol_error", "Invalid execution status.")
        if not isinstance(payload.get("stdout"), str) or not isinstance(
            payload.get("is_final"), bool
        ):
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid ExecutionResult payload."
            )
        if type(payload.get("interpreter_generation")) is not int:
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid interpreter generation."
            )
        if payload["is_final"]:
            validate_final_value(payload.get("final"))
        elif payload.get("final") is not None:
            raise CodingProtocolError(
                "ipc_protocol_error", "Non-final result contains final value."
            )
