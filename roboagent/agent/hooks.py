"""Deterministic typed Run lifecycle hooks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from roboagent.context import ModelContext
from roboagent.model import ModelResponse
from roboagent.message import ToolCall
from roboagent.runtime.types import RunContext, RunError, RunStatus
from roboagent.tool import ToolEffectRecord, ToolExecutionResult


class HookDecision(Enum):
    CONTINUE = "continue"
    FAIL_RUN = "fail_run"


@dataclass(frozen=True, slots=True)
class RunHookContext:
    run_context: RunContext


@dataclass(frozen=True, slots=True)
class ModelHookContext:
    run_context: RunContext
    model_context: ModelContext


@dataclass(frozen=True, slots=True)
class ToolHookContext:
    run_context: RunContext


@dataclass(frozen=True, slots=True)
class RunEndHookContext:
    run_context: RunContext
    provisional_status: RunStatus
    primary_error: RunError | None
    effects: tuple[ToolEffectRecord, ...]


class RunHook(Protocol):
    async def on_run_start(self, context: RunHookContext) -> None: ...

    async def before_model(self, context: ModelHookContext) -> HookDecision: ...

    async def after_model(self, context: ModelHookContext, response: ModelResponse) -> None: ...

    async def before_tool(self, context: ToolHookContext, call: ToolCall) -> HookDecision: ...

    async def after_tool(self, context: ToolHookContext, result: ToolExecutionResult) -> None: ...

    async def on_run_end(self, context: RunEndHookContext) -> None: ...
