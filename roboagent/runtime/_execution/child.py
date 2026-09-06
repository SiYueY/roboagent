"""Stable child-Run request/response and executor SPI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from roboagent.agent import Agent, RunConfig, RunResult
    from roboagent.runtime.execution import RuntimeToolExecutionContext


@dataclass(frozen=True, slots=True)
class ChildRunRequest:
    """The complete, transport-neutral request for one delegated Agent Run.

    A child Run always receives an isolated Session. Its usage and effects are
    owned by the child execution scope and are also included once in the root
    Run aggregate; the parent Agent-Tool does not synthesize a second effect.
    """

    agent: Agent
    task: str
    session_factory: object | None = None
    run_config: RunConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("Child Run task must not be blank.")


@dataclass(frozen=True, slots=True)
class ChildRunResult:
    """Result returned to the parent Tool after child lifecycle settlement."""

    result: RunResult


class ChildRunExecutor(Protocol):
    async def run_child(
        self, request: ChildRunRequest, parent: RuntimeToolExecutionContext
    ) -> ChildRunResult: ...
