"""Immutable contracts shared by the model, tool, and agent layers."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from time import time
from typing import Any, Literal, Mapping, Protocol

def _now() -> float:
    return time()

class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...
    @property
    def reason(self) -> str | None: ...

@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    raw_arguments: str = ""
    arguments: Mapping[str, Any] | None = None
    parse_error: str | None = None
    def __post_init__(self) -> None:
        if self.arguments is not None and not self.raw_arguments:
            object.__setattr__(self, "raw_arguments", json.dumps(dict(self.arguments), ensure_ascii=False))

@dataclass(frozen=True, slots=True)
class UserMessage:
    content: str
    timestamp: float = field(default_factory=_now)
    role: Literal["user"] = "user"

@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    timestamp: float = field(default_factory=_now)
    role: Literal["assistant"] = "assistant"

@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    details: Any = None
    error_code: str | None = None
    timestamp: float = field(default_factory=_now)
    role: Literal["tool"] = "tool"

Message = UserMessage | AssistantMessage | ToolResultMessage

@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    content: str
    details: Any = None
    is_error: bool = False
    error_code: str | None = None
    stop_run: bool = False

@dataclass(frozen=True, slots=True)
class ModelContext:
    system_prompt: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]

@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    context: ModelContext

@dataclass(frozen=True, slots=True)
class ModelEvent:
    type: Literal["start", "text_delta", "tool_call_delta", "done", "error", "cancelled"]
    delta: str = ""
    tool_call_index: int | None = None
    message: AssistantMessage | None = None
    error: str | None = None
