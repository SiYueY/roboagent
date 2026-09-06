"""Public CodingSession API layered on the canonical RoboAgent runtime."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roboagent import Agent
from roboagent.agent import Run, RunConfig, RunResult, Session
from roboagent.context import PromptInput
from roboagent.message import FrozenJsonObject, UserMessage
from roboagent.tool import (
    CompositeToolOutcome,
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectReporting,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolJsonContent,
)

from .client import WorkerClient
from .model_adapter import CodingModelAdapter, CodingRunState
from .protocol import EXECUTE_PROTOCOL, CodingProtocolError, validate_final_value
from .schema import PythonToolSpec, project_tools, render_tool_signatures


@dataclass(frozen=True, slots=True)
class CodingConfig:
    max_provider_calls: int = 16
    max_protocol_retries: int = 1
    startup_timeout: float = 10.0
    execution_timeout: float = 120.0
    max_code_bytes: int = 64 * 1024
    max_stdout_bytes: int = 64 * 1024
    max_observation_bytes: int = 64 * 1024
    inline_observation_bytes: int = 16 * 1024
    max_final_output_bytes: int = 64 * 1024
    max_ipc_frame_bytes: int = 1024 * 1024
    max_tool_requests_per_step: int = 64
    max_observation_artifact_bytes_per_session: int = 256 * 1024 * 1024
    observation_root: Path = field(default_factory=lambda: Path(".roboagent/artifacts"))

    def __post_init__(self) -> None:
        integer_names = (
            "max_provider_calls",
            "max_protocol_retries",
            "max_code_bytes",
            "max_stdout_bytes",
            "max_observation_bytes",
            "inline_observation_bytes",
            "max_final_output_bytes",
            "max_ipc_frame_bytes",
            "max_tool_requests_per_step",
            "max_observation_artifact_bytes_per_session",
        )
        for name in integer_names:
            value = getattr(self, name)
            minimum = 0 if name == "max_protocol_retries" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}.")
        for name in ("startup_timeout", "execution_timeout"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive.")
        if self.inline_observation_bytes > self.max_observation_bytes:
            raise ValueError(
                "inline_observation_bytes cannot exceed max_observation_bytes."
            )
        if not isinstance(self.observation_root, Path):
            object.__setattr__(self, "observation_root", Path(self.observation_root))


@dataclass(slots=True)
class _Observation:
    path: Path
    size: int
    status: str = "ORPHANED"


class ObservationStore:
    def __init__(self, root: Path, session_random: str, quota: int) -> None:
        self.root = root
        self.session_random = session_random
        self.quota = quota
        self.counter = 0
        self.total = 0
        self.items: dict[str, _Observation] = {}

    def write(self, data: bytes) -> dict[str, object]:
        if self.total + len(data) > self.quota:
            raise CodingProtocolError(
                "observation_storage_limit_exceeded",
                "Observation artifact session quota exceeded.",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.counter += 1
        identifier = f"obs_{self.session_random}_{self.counter}"
        path = self.root / identifier
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{identifier}.", dir=self.root
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self.items[identifier] = _Observation(path, len(data))
        self.total += len(data)
        return {"id": identifier, "path": str(path), "size": len(data)}

    def mark_referenced(self, identifier: str) -> None:
        item = self.items.get(identifier)
        if item is not None:
            item.status = "REFERENCED"

    def close(self) -> None:
        for item in self.items.values():
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                pass
        self.items.clear()
        self.total = 0


class CodingSession:
    def __init__(
        self,
        session: Session,
        adapter: CodingModelAdapter,
        worker: WorkerClient,
        config: CodingConfig,
        store: ObservationStore,
    ) -> None:
        self._session = session
        self.adapter = adapter
        self.worker = worker
        self.config = config
        self.observations = store
        self._active_run: Run | None = None
        self._finalizer: asyncio.Task[RunResult] | None = None

    @property
    def session(self) -> Session:
        return self._session

    @property
    def active_run(self) -> Run | None:
        """Current canonical Run, if any, for event subscription/cancellation UX."""
        return self._active_run

    async def run(
        self,
        message: UserMessage | str,
        *,
        run_config: RunConfig | None = None,
    ) -> RunResult:
        self.start(message, run_config=run_config)
        assert self._finalizer is not None
        return await asyncio.shield(self._finalizer)

    def start(
        self,
        message: UserMessage | str,
        *,
        run_config: RunConfig | None = None,
    ) -> Run:
        user = UserMessage(message) if isinstance(message, str) else message
        if not isinstance(user, UserMessage):
            raise TypeError("CodingSession.start accepts UserMessage or str.")
        run = self._session.start(user, config=run_config)
        state = CodingRunState(run.run_id, self.config.max_provider_calls)
        self.adapter.bind(state)
        self._active_run = run
        self._finalizer = asyncio.create_task(self._finalize(run, state))
        return run

    async def wait(self, run: Run) -> RunResult:
        """Wait for a started Run and its CodingSession-owned finalization."""
        if self._active_run is run and self._finalizer is not None:
            return await asyncio.shield(self._finalizer)
        return await run.result()

    async def _finalize(self, run: Run, state: CodingRunState) -> RunResult:
        try:
            result = await run.result()
            self._mark_committed_observations()
            return result
        finally:
            if self._active_run is run:
                self._active_run = None
            self.adapter.unbind(state)

    async def steer(self, message: UserMessage | str) -> None:
        user = UserMessage(message) if isinstance(message, str) else message
        if not isinstance(user, UserMessage):
            raise TypeError("CodingSession.steer accepts UserMessage or str.")
        await self._session.steer(user)

    def cancel(self) -> None:
        run = self._active_run
        if run is not None:
            run.cancel()

    async def close(self) -> None:
        run = self._active_run
        if run is not None:
            run.cancel()
            if self._finalizer is not None:
                await asyncio.shield(self._finalizer)
            else:
                await run.result()
        await self.worker.close()
        self.observations.close()
        if not self._session.closed:
            await self._session.close()

    def _mark_committed_observations(self) -> None:
        from roboagent.message import JsonContent, ToolResultMessage, thaw_json

        for message in self._session.messages:
            if (
                not isinstance(message, ToolResultMessage)
                or message.tool_name != "execute_python"
            ):
                continue
            for content in message.content:
                if isinstance(content, JsonContent):
                    value = thaw_json(content.value)
                    if isinstance(value, dict) and isinstance(
                        value.get("observation_file"), dict
                    ):
                        identifier = value["observation_file"].get("id")
                        if isinstance(identifier, str):
                            self.observations.mark_referenced(identifier)


def create_coding_session(
    base_agent: Agent,
    *,
    config: CodingConfig | None = None,
    unsafe_python: bool = False,
    session_id: str | None = None,
    **session_options: Any,
) -> CodingSession:
    if os.name != "posix":
        raise CodingProtocolError(
            "coding_platform_unsupported", "The coding harness requires POSIX."
        )
    if not isinstance(base_agent, Agent):
        raise TypeError("base_agent must be a canonical Agent.")
    if not isinstance(unsafe_python, bool):
        raise TypeError("unsafe_python must be bool.")
    effective = config or CodingConfig()
    specs = project_tools(base_agent.tool_registry.definitions())
    worker = WorkerClient(effective, trusted=unsafe_python)
    adapter = CodingModelAdapter(
        base_agent.model, max_protocol_retries=effective.max_protocol_retries
    )
    adapter.worker_client = worker
    store = ObservationStore(
        effective.observation_root,
        secrets.token_hex(16),
        effective.max_observation_artifact_bytes_per_session,
    )
    exposed_names = {item.canonical_name for item in specs}
    side_effecting = unsafe_python or any(
        base_agent.tool_registry.get(name).effect_kind is ToolEffectKind.SIDE_EFFECTING  # type: ignore[union-attr]
        for name in exposed_names
    )
    execute_python = _create_execute_python(
        worker, specs, store, effective, side_effecting
    )
    registry = base_agent.tool_registry.snapshot()
    registry.register(execute_python)
    prompt = _coding_prompt(base_agent.prompt, specs)
    derived = Agent(
        adapter,
        tool_registry=registry,
        prompt=prompt,
        context_manager=base_agent.context_manager,
        hooks=base_agent.hooks,
        tool_policy=base_agent.tool_policy,
        default_run_config=base_agent.default_run_config,
        media_limits=base_agent.media_limits,
        skill_manager=base_agent.skill_manager,
        approval_provider=base_agent.approval_provider,
        approval_settings=base_agent.approval_settings,
    )
    session = derived.new_session(session_id=session_id, **session_options)
    return CodingSession(session, adapter, worker, effective, store)


def _create_execute_python(
    worker: WorkerClient,
    specs: tuple[PythonToolSpec, ...],
    store: ObservationStore,
    config: CodingConfig,
    side_effecting: bool,
) -> Tool:
    effect_kind = (
        ToolEffectKind.SIDE_EFFECTING if side_effecting else ToolEffectKind.READ_ONLY
    )
    definition = ToolDefinition(
        "execute_python",
        "Execute one Python action through the RoboAgent coding worker.",
        FrozenJsonObject(
            {
                "type": "object",
                "properties": {"code": {"type": "string", "minLength": 1}},
                "required": ["code"],
                "additionalProperties": False,
            }
        ),
    )

    async def execute(arguments, context):
        code = arguments["code"]
        assert isinstance(code, str)
        if context.execution is None:
            raise ToolExecutionFailure(
                ToolErrorInfo(
                    "executor_failure", "Nested execution context is unavailable."
                )
            )
        try:
            result = await worker.execute(code, specs, context.execution)
        except CodingProtocolError as exc:
            if exc.code == "execution_timeout":
                result = {
                    "execution_status": "error",
                    "stdout": "TimeoutError: Python execution timed out.",
                    "is_final": False,
                    "final": None,
                    "interpreter_generation": worker.generation,
                }
            else:
                raise ToolExecutionFailure(
                    ToolErrorInfo("executor_failure", str(exc))
                ) from exc
        stdout = result.get("stdout", "")
        if not isinstance(stdout, str):
            raise ToolExecutionFailure(
                ToolErrorInfo("executor_failure", "Worker returned invalid stdout.")
            )
        stdout_bytes = stdout.encode("utf-8")
        if len(stdout_bytes) > config.max_stdout_bytes:
            stdout_bytes = stdout_bytes[: config.max_stdout_bytes]
            stdout = stdout_bytes.decode("utf-8", "ignore") + "\n[stdout truncated]"
        observation_bytes = stdout.encode("utf-8")[: config.max_observation_bytes]
        observation = observation_bytes.decode("utf-8", "ignore")
        observation_file: dict[str, object] | None = None
        if len(observation_bytes) > config.inline_observation_bytes:
            try:
                observation_file = store.write(observation_bytes)
            except CodingProtocolError as exc:
                raise ToolExecutionFailure(ToolErrorInfo(exc.code, str(exc))) from exc
            observation = observation_bytes[: config.inline_observation_bytes].decode(
                "utf-8", "ignore"
            )
        final = result.get("final")
        if result.get("is_final") is True:
            try:
                final = validate_final_value(final)
                final_bytes = json.dumps(
                    final, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            except (CodingProtocolError, TypeError, ValueError) as exc:
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "executor_failure", "Worker returned invalid final value."
                    )
                ) from exc
            if len(final_bytes) > config.max_final_output_bytes:
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "executor_failure", "Final output exceeds its byte limit."
                    )
                )
        envelope = {
            "protocol": EXECUTE_PROTOCOL,
            "execution_status": result.get("execution_status", "error"),
            "is_final": result.get("is_final") is True,
            "observation": observation,
            "observation_file": observation_file,
            "interpreter_reset": worker.pending_reset_notice,
            "reset_reason": "worker_generation_reset"
            if worker.pending_reset_notice
            else None,
        }
        if envelope["is_final"]:
            envelope["final"] = final
        return CompositeToolOutcome((ToolJsonContent(FrozenJsonObject(envelope)),))

    return Tool(
        definition,
        execute,
        effect_kind=effect_kind,
        effect_reporting=ToolEffectReporting.COMPOSITE,
    )


def _coding_prompt(
    prompt: PromptInput | None, specs: tuple[PythonToolSpec, ...]
) -> PromptInput:
    instructions = (
        "RoboAgent coding protocol: respond with plain final text, or exactly one fenced Python block. "
        "Use plain final text only after the user's task is complete. To inspect, test, or modify the "
        "workspace, emit a fenced Python block. Historical 'Python action:' lines are transcript "
        "projections, not valid action syntax; never emit that label. Call final_answer(value) inside "
        "Python to finish locally.\n" + render_tool_signatures(specs)
    )
    if prompt is None:
        return PromptInput(instructions)
    system = (
        instructions if prompt.system is None else f"{prompt.system}\n\n{instructions}"
    )
    return PromptInput(system, prompt.variables)


__all__ = ["CodingConfig", "CodingRunState", "CodingSession", "create_coding_session"]
