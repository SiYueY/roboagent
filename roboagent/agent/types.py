"""Public V1 Agent execution configuration and results."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping

from roboagent.message import AssistantMessage
from roboagent.runtime.types import RunError, RunTerminationReason, RunStatus

class ToolExecutionMode(Enum): SEQUENTIAL="sequential"; PARALLEL="parallel"
@dataclass(frozen=True, slots=True)
class ToolExecutionConfig:
    mode: ToolExecutionMode = ToolExecutionMode.SEQUENTIAL
    max_concurrency: int | None = None
    def __post_init__(self) -> None:
        if self.max_concurrency is not None and self.max_concurrency < 1: raise ValueError("max_concurrency must be positive.")
ToolPolicyFactory = Callable[[object], object]
@dataclass(frozen=True, slots=True)
class RunConfig:
    max_turns: int = 32
    timeout: float | None = None
    tool_execution: ToolExecutionConfig = field(default_factory=ToolExecutionConfig)
    tool_policy_factory: ToolPolicyFactory | None = None
    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.timeout is not None and self.timeout <= 0: raise ValueError("Invalid RunConfig.")
@dataclass(frozen=True, slots=True)
class PendingControl:
    sequence: int; kind: str; message: object
@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus; final_message: AssistantMessage | None; turns: int; termination_reason: RunTerminationReason; error: RunError | None = None; uncommitted_controls: tuple[PendingControl, ...] = ()
