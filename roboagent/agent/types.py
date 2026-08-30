"""Agent-local outcomes and hook contracts."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from roboagent.runtime import AssistantMessage, Message, ModelContext, ToolExecutionResult
from roboagent.tool import ToolInvocation

AgentRunStatus = Literal["completed", "failed", "cancelled", "max_turns", "timed_out"]

@dataclass(frozen=True, slots=True)
class AgentRunResult:
    messages: tuple[Message, ...]
    final_message: AssistantMessage | None
    status: AgentRunStatus
    error: str | None = None
    run_id: str = ""

ContextTransform = Callable[[ModelContext, object], ModelContext | Awaitable[ModelContext]]

@dataclass(frozen=True, slots=True)
class ToolCallDecision:
    allow: bool = True
    reason: str | None = None
    stop_run: bool = False

@dataclass(frozen=True, slots=True)
class ToolResultOverride:
    content: str | None = None
    details: Any = None
    is_error: bool | None = None
    error_code: str | None = None
    stop_run: bool | None = None

BeforeToolCall = Callable[[ToolInvocation], ToolCallDecision | Awaitable[ToolCallDecision | None] | None]
AfterToolCall = Callable[[ToolInvocation, ToolExecutionResult], ToolResultOverride | Awaitable[ToolResultOverride | None] | None]

@dataclass(frozen=True, slots=True)
class AgentHooks:
    context_transforms: tuple[ContextTransform, ...] = ()
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
