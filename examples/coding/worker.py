"""Separate coding worker speaking the V1.3 length-prefixed JSON protocol."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from dataclasses import dataclass

from .evaluator import AstEvaluator
from .protocol import (
    ArtifactHandle,
    CodingProtocolError,
    IpcEnvelope,
    RoboAgentToolError,
    decode_frame,
    encode_frame,
    validate_direction,
)


@dataclass(frozen=True, slots=True)
class _WorkerToolSpec:
    alias: str
    canonical_name: str
    properties: tuple[str, ...]
    required: frozenset[str]
    defaults: dict[str, object]

    def arguments(
        self, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> dict[str, object]:
        if len(args) > len(self.properties):
            raise TypeError("too many positional arguments")
        result = dict(zip(self.properties, args, strict=False))
        for name, value in kwargs.items():
            if name not in self.properties or name in result:
                raise TypeError("invalid or duplicate keyword argument")
            result[name] = value
        for name, value in self.defaults.items():
            result.setdefault(name, value)
        if self.required - result.keys():
            raise TypeError("missing required arguments")
        return result


class Worker:
    def __init__(
        self,
        sock: socket.socket,
        generation: int,
        max_frame: int,
        *,
        trusted: bool = False,
    ) -> None:
        self.sock = sock
        self.generation = generation
        self.max_frame = max_frame
        self.execution_id = ""
        self.execute_request_id = ""
        self.counter = 0
        self.specs: dict[str, _WorkerToolSpec] = {}
        self.evaluator = AstEvaluator(self.call_tool, trusted=trusted)

    def send(
        self, type_: str, payload: dict[str, object], *, request_id: str = ""
    ) -> None:
        envelope = IpcEnvelope(
            self.generation,
            self.execution_id,
            request_id,
            type_,
            payload,
        )
        self.sock.sendall(encode_frame(envelope, self.max_frame))

    def receive(self) -> IpcEnvelope:
        header = self._read_exact(4)
        if header is None:
            raise EOFError
        length = int.from_bytes(header, "big")
        if length > self.max_frame:
            raise CodingProtocolError("ipc_protocol_error", "IPC frame exceeds limit.")
        body = self._read_exact(length)
        if body is None:
            raise CodingProtocolError("ipc_protocol_error", "Partial IPC frame.")
        envelope = decode_frame(header + body, self.max_frame)
        validate_direction(envelope, "host")
        if envelope.worker_generation != self.generation:
            raise CodingProtocolError("ipc_protocol_error", "Stale worker generation.")
        return envelope

    def _read_exact(self, size: int) -> bytes | None:
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def call_tool(
        self, alias: str, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> object:
        spec = self.specs.get(alias)
        if spec is None:
            raise NameError(alias)
        arguments = spec.arguments(args, kwargs)
        self.counter += 1
        request_id = f"tool_{self.counter}"
        self.send(
            "tool_request",
            {"tool_name": spec.canonical_name, "arguments": arguments},
            request_id=request_id,
        )
        response = self.receive()
        if (
            response.type != "tool_response"
            or response.execution_id != self.execution_id
            or response.request_id != request_id
        ):
            raise CodingProtocolError(
                "ipc_protocol_error", "Tool response identity mismatch."
            )
        payload = response.payload
        if payload.get("ok") is True:
            return _decode_tool_value(payload.get("value"))
        error = payload.get("error")
        if not isinstance(error, dict):
            raise CodingProtocolError(
                "ipc_protocol_error", "Invalid Tool error response."
            )
        raise RoboAgentToolError(
            str(error.get("code", "tool_error")),
            str(error.get("message", "Tool failed.")),
            error.get("retryable") is True,
        )

    def run(self) -> None:
        self.send("hello", {"pid": os.getpid()})
        accepted = self.receive()
        if accepted.type != "accepted":
            raise CodingProtocolError(
                "ipc_protocol_error", "Expected accepted handshake."
            )
        self.send("ready", {"interpreter_generation": self.generation})
        while True:
            message = self.receive()
            if message.type == "shutdown":
                return
            if message.type != "execute":
                raise CodingProtocolError(
                    "ipc_protocol_error", "Expected execute request."
                )
            self.execution_id = message.execution_id
            self.execute_request_id = message.request_id
            code = message.payload.get("code")
            raw_specs = message.payload.get("tool_specs", [])
            if not isinstance(code, str) or not isinstance(raw_specs, list):
                raise CodingProtocolError(
                    "ipc_protocol_error", "Invalid execute payload."
                )
            self.specs = _parse_specs(raw_specs)
            result = self.evaluator.execute(code, set(self.specs))
            payload: dict[str, object] = {
                "execution_status": "error" if result.error else "ok",
                "stdout": result.stdout
                if result.error is None
                else f"{result.stdout}{result.error}",
                "is_final": result.is_final,
                "final": result.final,
                "interpreter_generation": self.generation,
            }
            self.send("execution_result", payload, request_id=self.execute_request_id)
            self.execution_id = ""
            self.execute_request_id = ""
            self.specs = {}


def _parse_specs(values: list[object]) -> dict[str, _WorkerToolSpec]:
    result: dict[str, _WorkerToolSpec] = {}
    for value in values:
        if not isinstance(value, dict):
            raise CodingProtocolError("ipc_protocol_error", "Invalid worker Tool spec.")
        alias = value.get("alias")
        canonical = value.get("canonical_name")
        properties = value.get("properties")
        required = value.get("required")
        defaults = value.get("defaults")
        if (
            not isinstance(alias, str)
            or not isinstance(canonical, str)
            or not isinstance(properties, list)
        ):
            raise CodingProtocolError("ipc_protocol_error", "Invalid worker Tool spec.")
        if not isinstance(required, list) or not isinstance(defaults, dict):
            raise CodingProtocolError("ipc_protocol_error", "Invalid worker Tool spec.")
        result[alias] = _WorkerToolSpec(
            alias, canonical, tuple(properties), frozenset(required), defaults
        )
    return result


def _decode_tool_value(value: object) -> object:
    if not isinstance(value, dict):
        raise CodingProtocolError("ipc_protocol_error", "Invalid ToolValue.")
    kind, raw = value.get("kind"), value.get("value")
    if kind in {"text", "json"}:
        return raw
    if kind == "artifact" and isinstance(raw, dict):
        return ArtifactHandle(**raw)
    if kind == "tuple" and isinstance(raw, list):
        if any(isinstance(item, dict) and item.get("kind") == "tuple" for item in raw):
            raise CodingProtocolError(
                "ipc_protocol_error", "Nested ToolValue tuple is invalid."
            )
        return tuple(_decode_tool_value(item) for item in raw)
    raise CodingProtocolError("ipc_protocol_error", "Invalid ToolValue kind.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--max-frame", type=int, required=True)
    parser.add_argument("--trusted", action="store_true")
    args = parser.parse_args()
    sock = socket.socket(fileno=args.fd)
    try:
        Worker(sock, args.generation, args.max_frame, trusted=args.trusted).run()
    except (EOFError, CodingProtocolError):
        return 70
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
