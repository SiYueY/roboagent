"""Canonical Run configuration and final result."""

from __future__ import annotations

from dataclasses import dataclass, field

from roboagent.message import AssistantMessage
from roboagent.model import ModelSettings, Usage
from roboagent.runtime.types import RunError, RunStatus
from roboagent.tool import ToolEffectRecord, ToolExecutorConfig


@dataclass(frozen=True, slots=True)
class RunConfig:
    max_turns: int = 32
    timeout: float | None = None
    hook_timeout: float | None = 30.0
    cleanup_hook_timeout: float | None = 10.0
    model_settings: ModelSettings | None = None
    tool_executor: ToolExecutorConfig = field(default_factory=ToolExecutorConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.max_turns, int) or isinstance(self.max_turns, bool) or self.max_turns < 1:
            raise ValueError("max_turns must be positive.")
        for name in ("timeout", "hook_timeout", "cleanup_hook_timeout"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
                raise ValueError(f"{name} must be positive or None.")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    output: AssistantMessage | None
    usage: Usage | None
    error: RunError | None
    cleanup_errors: tuple[RunError, ...]
    effects: tuple[ToolEffectRecord, ...]
    retry_safe: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "cleanup_errors", tuple(self.cleanup_errors))
        object.__setattr__(self, "effects", tuple(self.effects))
        if not isinstance(self.run_id, str) or not self.run_id or not isinstance(self.status, RunStatus):
            raise ValueError("RunResult requires canonical identity and status.")
        if self.output is not None and not isinstance(self.output, AssistantMessage):
            raise TypeError("RunResult.output must be AssistantMessage or None.")
        if self.usage is not None and not isinstance(self.usage, Usage):
            raise TypeError("RunResult.usage must be Usage or None.")
        if self.error is not None and not isinstance(self.error, RunError):
            raise TypeError("RunResult.error must be RunError or None.")
        if self.status is RunStatus.COMPLETED and self.error is not None:
            raise ValueError("Completed RunResult cannot contain a primary error.")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("Failed RunResult requires a primary error.")
        if self.status is RunStatus.CANCELLED and self.error is not None:
            raise ValueError("Cancelled RunResult cannot contain a primary error.")
        if not all(isinstance(item, RunError) for item in self.cleanup_errors):
            raise TypeError("cleanup_errors must contain RunError values.")
        if not all(isinstance(item, ToolEffectRecord) for item in self.effects):
            raise TypeError("effects must contain ToolEffectRecord values.")
        if not isinstance(self.retry_safe, bool):
            raise TypeError("retry_safe must be bool.")
