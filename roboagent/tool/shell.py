"""Explicit non-interactive POSIX shell Tool."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass, field

from roboagent.message import FrozenJsonObject, freeze_json_object

from .filesystem import Workspace, validate_relative_path
from .tool import (
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolExecutionMode,
    ToolJsonContent,
)


@dataclass(frozen=True, slots=True)
class ShellConfig:
    workspace: Workspace
    max_command_bytes: int = 64 * 1024
    max_stdout_bytes: int = 256 * 1024
    max_stderr_bytes: int = 256 * 1024
    default_timeout: float | None = None
    max_timeout: float | None = None
    cancellation_grace_period: float = 2.0
    env: FrozenJsonObject | None = None
    _base_env: FrozenJsonObject = field(init=False, repr=False)

    def __post_init__(self) -> None:
        byte_limits = (self.max_command_bytes, self.max_stdout_bytes, self.max_stderr_bytes)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in byte_limits):
            raise ValueError("Shell byte limits must be positive.")
        if not isinstance(self.workspace, Workspace):
            raise TypeError("ShellConfig requires a Workspace.")
        if self.default_timeout is not None and (isinstance(self.default_timeout, bool) or not isinstance(self.default_timeout, (int, float)) or self.default_timeout <= 0):
            raise ValueError("default_timeout must be positive.")
        if self.max_timeout is not None and (isinstance(self.max_timeout, bool) or not isinstance(self.max_timeout, (int, float)) or self.max_timeout <= 0):
            raise ValueError("max_timeout must be positive.")
        if isinstance(self.cancellation_grace_period, bool) or not isinstance(self.cancellation_grace_period, (int, float)) or self.cancellation_grace_period < 0:
            raise ValueError("cancellation_grace_period must be non-negative.")
        overrides = freeze_json_object(self.env) if self.env is not None else FrozenJsonObject()
        if any(not isinstance(value, str) for value in overrides.values()):
            raise ValueError("Shell env must be a flat str -> str mapping.")
        object.__setattr__(self, "env", overrides if self.env is not None else None)
        object.__setattr__(self, "_base_env", FrozenJsonObject(dict(os.environ)))


def create_shell_tool(config: ShellConfig) -> Tool:
    if os.name != "posix":
        raise RuntimeError("The shell Tool requires a POSIX platform.")
    async def execute(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        command = arguments["command"]
        assert isinstance(command, str)
        if len(command.encode("utf-8")) > config.max_command_bytes:
            raise ToolExecutionFailure(ToolErrorInfo("command_too_large", "Command exceeds max_command_bytes."))
        cwd_value = arguments.get("cwd")
        relative = validate_relative_path(cwd_value or ".")
        cwd = config.workspace.root.joinpath(*relative.parts).resolve()
        if not cwd.is_relative_to(config.workspace.root) or not cwd.is_dir():
            raise ToolExecutionFailure(ToolErrorInfo("invalid_cwd", "Shell cwd is invalid."))
        environment = dict(config._base_env.items())
        if config.env is not None:
            environment.update(config.env)
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-lc",
            command,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_task = asyncio.create_task(_read_bounded(process.stdout, config.max_stdout_bytes))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr, config.max_stderr_bytes))
        try:
            await _wait_for_parent_exit(process)
            if not stdout_task.done() or not stderr_task.done():
                await _terminate(process, config.cancellation_grace_period)
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
        except asyncio.CancelledError:
            await _terminate(process, config.cancellation_grace_period)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        return ToolJsonContent(
            FrozenJsonObject(
                {
                    "exit_code": process.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                }
            )
        )

    def requested_timeout(arguments: FrozenJsonObject) -> float | None:
        value = arguments.get("timeout")
        if value is None:
            return config.default_timeout
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        timeout = float(value)
        return min(timeout, config.max_timeout) if config.max_timeout is not None else timeout

    schema = FrozenJsonObject(
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": ["string", "null"]},
                "timeout": {"type": ["number", "null"], "exclusiveMinimum": 0},
            },
            "required": ["command"],
            "additionalProperties": False,
        }
    )
    return Tool(
        ToolDefinition("shell", "Execute a non-interactive POSIX shell command in the workspace.", schema),
        execute,
        ToolExecutionMode.SERIAL,
        ToolEffectKind.SIDE_EFFECTING,
        config.default_timeout,
        requested_timeout,
    )


async def _read_bounded(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    kept = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        room = max(0, limit - len(kept))
        kept.extend(chunk[:room])
        truncated = truncated or len(chunk) > room
    return bytes(kept), truncated


async def _wait_for_parent_exit(process: asyncio.subprocess.Process) -> None:
    while process.returncode is None:
        await asyncio.sleep(0.01)


async def _terminate(process: asyncio.subprocess.Process, grace: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = asyncio.get_running_loop().time() + grace
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), grace)
        except TimeoutError:
            pass
    while _group_exists(process.pid) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    if _group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    if process.returncode is None:
        await process.wait()


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True
