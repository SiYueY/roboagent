"""Agent-local configuration, hooks, and observer contracts."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from roboagent.runtime import AgentEvent, ModelContext, ToolExecutionResult
from roboagent.tool import ToolInvocation

ContextTransform = Callable[[ModelContext], ModelContext | Awaitable[ModelContext]]
Observer = Callable[[AgentEvent], object]
@dataclass(frozen=True, slots=True)
class ToolCallDecision: allow: bool = True; reason: str | None = None; terminate: bool = False
@dataclass(frozen=True, slots=True)
class ToolResultOverride: content: str | None = None; details: Any = None; is_error: bool | None = None; terminate: bool | None = None
BeforeToolCall = Callable[[ToolInvocation], ToolCallDecision | Awaitable[ToolCallDecision | None] | None]
AfterToolCall = Callable[[ToolInvocation, ToolExecutionResult], ToolResultOverride | Awaitable[ToolResultOverride | None] | None]
@dataclass(frozen=True, slots=True)
class AgentHooks:
    context_transforms: tuple[ContextTransform, ...] = ()
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
