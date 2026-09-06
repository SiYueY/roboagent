"""Nested execution budget value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionBudgetConfig:
    max_agent_depth: int = 4
    max_child_runs: int = 32
    max_nested_tool_calls: int = 256

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.max_agent_depth,
                self.max_child_runs,
                self.max_nested_tool_calls,
            )
        ):
            raise ValueError("Execution budget values must be non-negative integers.")


@dataclass(frozen=True, slots=True)
class ExecutionBudgetView:
    max_agent_depth: int
    remaining_child_runs: int
    remaining_nested_tool_calls: int
