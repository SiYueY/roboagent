"""Native runtime extension contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from roboagent.runtime import ModelContext, ToolExecutionResult
from roboagent.tool import ToolInvocation

ContextTransform = Callable[[ModelContext], ModelContext | Awaitable[ModelContext]]


@dataclass(frozen=True, slots=True)
class ToolCallDecision:
    allow: bool = True
    reason: str | None = None
    terminate: bool = False


@dataclass(frozen=True, slots=True)
class ToolResultOverride:
    content: str | None = None
    details: Any = None
    is_error: bool | None = None
    terminate: bool | None = None


BeforeToolCall = Callable[[ToolInvocation], ToolCallDecision | Awaitable[ToolCallDecision | None] | None]
AfterToolCall = Callable[[ToolInvocation, ToolExecutionResult], ToolResultOverride | Awaitable[ToolResultOverride | None] | None]
