"""The canonical typed lifecycle events emitted by agent runs."""
from __future__ import annotations
from dataclasses import dataclass, field
from time import time
from typing import Literal
from .types import Message, ToolCall, ToolResultMessage

def _now() -> float:
    return time()

@dataclass(frozen=True, slots=True, kw_only=True)
class _Event:
    run_id: str
    sequence: int
    timestamp: float = field(default_factory=_now)

@dataclass(frozen=True, slots=True, kw_only=True)
class AgentStartedEvent(_Event):
    session_id: str
    type: Literal["agent_started"] = "agent_started"
@dataclass(frozen=True, slots=True, kw_only=True)
class AgentCompletedEvent(_Event):
    status: Literal["completed", "failed", "cancelled", "max_turns", "timed_out"]
    error: str | None = None
    type: Literal["agent_completed"] = "agent_completed"
@dataclass(frozen=True, slots=True, kw_only=True)
class TurnStartedEvent(_Event):
    turn: int
    type: Literal["turn_started"] = "turn_started"
@dataclass(frozen=True, slots=True, kw_only=True)
class TurnCompletedEvent(_Event):
    turn: int
    type: Literal["turn_completed"] = "turn_completed"
@dataclass(frozen=True, slots=True, kw_only=True)
class MessageStartedEvent(_Event):
    turn: int | None
    message: Message
    type: Literal["message_started"] = "message_started"
@dataclass(frozen=True, slots=True, kw_only=True)
class MessageDeltaEvent(_Event):
    turn: int
    delta: str
    kind: Literal["text", "tool_call"]
    tool_call_index: int | None = None
    type: Literal["message_delta"] = "message_delta"
@dataclass(frozen=True, slots=True, kw_only=True)
class MessageCompletedEvent(_Event):
    turn: int | None
    message: Message
    type: Literal["message_completed"] = "message_completed"
@dataclass(frozen=True, slots=True, kw_only=True)
class ToolStartedEvent(_Event):
    turn: int
    tool_call: ToolCall
    type: Literal["tool_started"] = "tool_started"
@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCompletedEvent(_Event):
    turn: int
    tool_call: ToolCall
    result: ToolResultMessage
    type: Literal["tool_completed"] = "tool_completed"
@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeErrorEvent(_Event):
    error: str
    turn: int | None = None
    type: Literal["runtime_error"] = "runtime_error"
AgentEvent = AgentStartedEvent | AgentCompletedEvent | TurnStartedEvent | TurnCompletedEvent | MessageStartedEvent | MessageDeltaEvent | MessageCompletedEvent | ToolStartedEvent | ToolCompletedEvent | RuntimeErrorEvent
