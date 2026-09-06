"""Canonical Run configuration and final result."""

from __future__ import annotations

from dataclasses import dataclass, field

from roboagent.message import AssistantMessage
from roboagent.model import ModelSettings, Usage
from roboagent.runtime import (
    CleanupError,
    ExecutionBudgetConfig,
    ExecutionRecord,
    RetryBlocker,
    RunError,
    RunStatus,
)
from roboagent.tool import ToolEffectRecord, ToolExecutorConfig


@dataclass(frozen=True, slots=True)
class RunConfig:
    max_turns: int = 32
    timeout: float | None = None
    hook_timeout: float | None = 30.0
    cleanup_hook_timeout: float | None = 10.0
    model_settings: ModelSettings | None = None
    tool_executor: ToolExecutorConfig = field(default_factory=ToolExecutorConfig)
    execution_budget: ExecutionBudgetConfig = field(
        default_factory=ExecutionBudgetConfig
    )
    settlement_timeout: float = 10.0
    cleanup_timeout: float = 5.0
    max_execution_records: int = 4096
    max_record_evidence_bytes: int = 4096
    max_child_artifact_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_turns, int)
            or isinstance(self.max_turns, bool)
            or self.max_turns < 1
        ):
            raise ValueError("max_turns must be positive.")
        for name in ("timeout", "hook_timeout", "cleanup_hook_timeout"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive or None.")
        if not isinstance(self.execution_budget, ExecutionBudgetConfig):
            raise TypeError("execution_budget must be ExecutionBudgetConfig.")
        for name in ("settlement_timeout", "cleanup_timeout"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive.")
        for name in (
            "max_execution_records",
            "max_record_evidence_bytes",
            "max_child_artifact_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    output: AssistantMessage | None
    usage: Usage | None
    error: RunError | None
    cleanup_errors: tuple[RunError | CleanupError, ...]
    effects: tuple[ToolEffectRecord, ...]
    retry_safe: bool
    usage_known: bool | None = None
    execution_records: tuple[ExecutionRecord, ...] = ()
    execution_records_complete: bool = True
    retry_blockers: tuple[RetryBlocker, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cleanup_errors", tuple(self.cleanup_errors))
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "execution_records", tuple(self.execution_records))
        object.__setattr__(self, "retry_blockers", tuple(self.retry_blockers))
        if (
            not isinstance(self.run_id, str)
            or not self.run_id
            or not isinstance(self.status, RunStatus)
        ):
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
        if not all(
            isinstance(item, (RunError, CleanupError)) for item in self.cleanup_errors
        ):
            raise TypeError("cleanup_errors must contain canonical cleanup errors.")
        if not all(isinstance(item, ToolEffectRecord) for item in self.effects):
            raise TypeError("effects must contain ToolEffectRecord values.")
        if not isinstance(self.retry_safe, bool):
            raise TypeError("retry_safe must be bool.")
        if self.usage_known not in (None, True, False):
            raise TypeError("usage_known must be bool or None.")
        if self.usage_known is True and self.usage is None:
            raise ValueError("Known usage requires a Usage value.")
        if self.usage_known is False and self.usage is not None:
            raise ValueError("Unknown usage cannot contain a Usage value.")
        if not all(
            isinstance(item, ExecutionRecord) for item in self.execution_records
        ):
            raise TypeError("execution_records must be canonical.")
        if not isinstance(self.execution_records_complete, bool):
            raise TypeError("execution_records_complete must be bool.")
        if not all(isinstance(item, RetryBlocker) for item in self.retry_blockers):
            raise TypeError("retry_blockers must be canonical.")
