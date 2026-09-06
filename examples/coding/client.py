"""Async host client for the process-isolated Python worker."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from roboagent.runtime import SettlementHandler, ToolExecutionContext

from .bridge import CodeToolBridge
from .protocol import (
    CodingProtocolError,
    IpcEnvelope,
    decode_frame,
    encode_frame,
    validate_direction,
)
from .schema import PythonToolSpec


class WorkerConfig(Protocol):
    @property
    def max_code_bytes(self) -> int: ...
    @property
    def execution_timeout(self) -> float: ...
    @property
    def max_tool_requests_per_step(self) -> int: ...
    @property
    def max_ipc_frame_bytes(self) -> int: ...
    @property
    def startup_timeout(self) -> float: ...


class WorkerClient:
    def __init__(self, config: WorkerConfig, *, trusted: bool = False) -> None:
        self.config = config
        self.trusted = trusted
        self.generation = 1
        self.pending_reset_notice = False
        self._process: subprocess.Popen[bytes] | None = None
        self._socket: socket.socket | None = None
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self._lease = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return (
            self._process is not None
            and self._process.poll() is None
            and self._socket is not None
        )

    async def execute(
        self,
        code: str,
        specs: tuple[PythonToolSpec, ...],
        execution: ToolExecutionContext,
    ) -> dict[str, object]:
        if len(code.encode("utf-8")) > self.config.max_code_bytes:
            raise CodingProtocolError(
                "code_too_large", "Python code exceeds its byte limit."
            )
        async with self._lease:
            try:
                await self._ensure_started()
            except CodingProtocolError:
                raise
            except BaseException as exc:
                await self._terminate_reset()
                raise CodingProtocolError(
                    "worker_startup_failure", "Python worker could not start."
                ) from exc
            if self.trusted:
                from roboagent.runtime import RetryBlockerCode

                execution.add_retry_blocker(
                    RetryBlockerCode.TRUSTED_EXECUTION,
                    "Trusted Python execution began and may have unobserved host effects.",
                )
            execution_id = uuid4().hex
            request_id = uuid4().hex
            bridge = CodeToolBridge(execution, specs)
            seen_tool_requests: set[str] = set()
            payload_specs = [
                {
                    "alias": spec.alias,
                    "canonical_name": spec.canonical_name,
                    "properties": [name for name, _ in spec.properties],
                    "required": sorted(spec.required),
                    "defaults": {
                        name: schema["default"]
                        for name, schema in spec.properties
                        if "default" in schema
                    },
                }
                for spec in specs
            ]
            await self._send(
                IpcEnvelope(
                    self.generation,
                    execution_id,
                    request_id,
                    "execute",
                    {
                        "code": code,
                        "tool_names": [item.canonical_name for item in specs],
                        "tool_specs": payload_specs,
                    },
                )
            )
            tool_requests = 0
            deadline = asyncio.get_running_loop().time() + self.config.execution_timeout
            if execution.deadline is not None:
                deadline = min(deadline, execution.deadline)
            try:
                while True:
                    timeout = deadline - asyncio.get_running_loop().time()
                    if timeout <= 0:
                        raise TimeoutError
                    message = await asyncio.wait_for(self._receive(), timeout)
                    if (
                        message.worker_generation != self.generation
                        or message.execution_id != execution_id
                    ):
                        raise CodingProtocolError(
                            "ipc_protocol_error", "Worker identity mismatch."
                        )
                    if message.type == "tool_request":
                        is_new_request = message.request_id not in seen_tool_requests
                        if is_new_request:
                            seen_tool_requests.add(message.request_id)
                            tool_requests += 1
                        if tool_requests > self.config.max_tool_requests_per_step:
                            response = {
                                "ok": False,
                                "error": {
                                    "code": "tool_request_budget_exceeded",
                                    "message": "Python Tool request budget exceeded.",
                                    "retryable": False,
                                },
                            }
                        elif execution.cancellation.cancelled:
                            response = {
                                "ok": False,
                                "error": {
                                    "code": "execution_cancelled",
                                    "message": "Execution is cancelling.",
                                    "retryable": False,
                                },
                            }
                        else:
                            tool_name = message.payload.get("tool_name")
                            arguments = message.payload.get("arguments")
                            if not isinstance(tool_name, str) or not isinstance(
                                arguments, dict
                            ):
                                raise CodingProtocolError(
                                    "ipc_protocol_error", "Invalid ToolRequest payload."
                                )
                            response = await bridge.execute(
                                message.request_id, tool_name, arguments
                            )
                        await self._send(
                            IpcEnvelope(
                                self.generation,
                                execution_id,
                                message.request_id,
                                "tool_response",
                                response,
                            )
                        )
                    elif message.type == "execution_result":
                        if message.request_id != request_id:
                            raise CodingProtocolError(
                                "ipc_protocol_error",
                                "ExecutionResult identity mismatch.",
                            )
                        return message.payload
                    else:
                        raise CodingProtocolError(
                            "ipc_protocol_error", "Unexpected worker message."
                        )
            except TimeoutError as exc:
                await self._terminate_reset()
                raise CodingProtocolError(
                    "execution_timeout", "Python execution timed out."
                ) from exc
            except asyncio.CancelledError:
                async with execution.settlement_barrier(
                    handler=_WorkerSettlement(self)
                ):
                    try:
                        await self._send(
                            IpcEnvelope(
                                self.generation, execution_id, request_id, "cancel", {}
                            )
                        )
                    except Exception:
                        pass
                    await self._terminate_reset()
                raise
            except CodingProtocolError:
                await self._terminate_reset()
                raise
            except (EOFError, OSError) as exc:
                await self._terminate_reset()
                raise CodingProtocolError(
                    "executor_failure", "Python worker failed."
                ) from exc

    async def close(self) -> None:
        if self.alive:
            try:
                await self._send(IpcEnvelope(self.generation, "", "", "shutdown", {}))
                assert self._process is not None
                await asyncio.wait_for(asyncio.to_thread(self._process.wait), 1.0)
            except Exception:
                await self._terminate(mark_reset=False)
        self._close_handles()

    async def force_close(self) -> None:
        await self._terminate(mark_reset=False)

    async def _ensure_started(self) -> None:
        if self.alive:
            return
        await self._start()

    async def _start(self) -> None:
        parent, child = socket.socketpair()
        parent.setblocking(False)
        self._temp = tempfile.TemporaryDirectory(prefix="roboagent-coding-")
        package_root = str(Path(__file__).resolve().parents[2])
        bootstrap = (
            "import sys;sys.path.insert(0," + repr(package_root) + ");"
            "from examples.coding.worker import main;raise SystemExit(main())"
        )
        env = _worker_environment() if not self.trusted else dict(os.environ)
        command = [
            sys.executable,
            "-I",
            "-c",
            bootstrap,
            "--fd",
            str(child.fileno()),
            "--generation",
            str(self.generation),
            "--max-frame",
            str(self.config.max_ipc_frame_bytes),
        ]
        if self.trusted:
            command.append("--trusted")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=self._temp.name,
                env=env,
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            child.close()
        self._socket = parent
        try:
            async with asyncio.timeout(self.config.startup_timeout):
                hello = await self._receive()
                if hello.type != "hello" or hello.worker_generation != self.generation:
                    raise CodingProtocolError(
                        "ipc_protocol_error", "Invalid worker hello."
                    )
                await self._send(IpcEnvelope(self.generation, "", "", "accepted", {}))
                ready = await self._receive()
                if ready.type != "ready" or ready.worker_generation != self.generation:
                    raise CodingProtocolError(
                        "ipc_protocol_error", "Invalid worker ready."
                    )
        except TimeoutError as exc:
            await self._terminate_reset()
            raise CodingProtocolError(
                "worker_startup_timeout", "Python worker startup timed out."
            ) from exc
        except BaseException:
            await self._terminate_reset()
            raise

    async def _send(self, envelope: IpcEnvelope) -> None:
        if self._socket is None:
            raise EOFError
        await asyncio.get_running_loop().sock_sendall(
            self._socket,
            encode_frame(envelope, self.config.max_ipc_frame_bytes),
        )

    async def _receive(self) -> IpcEnvelope:
        if self._socket is None:
            raise EOFError
        header = await _recv_exact(self._socket, 4)
        length = int.from_bytes(header, "big")
        if length > self.config.max_ipc_frame_bytes:
            raise CodingProtocolError("ipc_protocol_error", "IPC frame exceeds limit.")
        body = await _recv_exact(self._socket, length)
        message = decode_frame(header + body, self.config.max_ipc_frame_bytes)
        validate_direction(message, "worker")
        return message

    async def _terminate_reset(self) -> None:
        await self._terminate(mark_reset=True)

    async def _terminate(self, *, mark_reset: bool) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), 1.0)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.to_thread(process.wait)
        self._close_handles()
        if mark_reset:
            self.generation += 1
            self.pending_reset_notice = True

    def _close_handles(self) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._process = None
        if self._temp is not None:
            self._temp.cleanup()
        self._temp = None


class _WorkerSettlement(SettlementHandler):
    def __init__(self, client: WorkerClient) -> None:
        self.client = client

    async def settle(self) -> None:
        await self.client._terminate_reset()

    async def force_settle(self) -> None:
        await self.client._terminate_reset()


async def _recv_exact(sock: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = await asyncio.get_running_loop().sock_recv(sock, size - len(result))
        if not chunk:
            if result:
                raise CodingProtocolError("ipc_protocol_error", "Partial IPC frame.")
            raise EOFError
        result.extend(chunk)
    return bytes(result)


def _worker_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONPATH": "", "PYTHONSTARTUP": ""})
    return env
