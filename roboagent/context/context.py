"""Model-facing context values, separate from the session transcript."""

from __future__ import annotations

from dataclasses import dataclass

from roboagent.runtime import Message


@dataclass(frozen=True, slots=True)
class AgentContext:
    """The working conversation view used for one model invocation."""

    messages: tuple[Message, ...]
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class SessionContextState:
    """Small, persistent cursor state for deriving an agent context."""

    summary: str | None = None
    compacted_until: int = 0

    def __post_init__(self) -> None:
        if self.compacted_until < 0:
            raise ValueError("compacted_until cannot be negative.")


@dataclass(frozen=True, slots=True)
class ContextResult:
    """The model-facing context and state to persist with the session."""

    context: AgentContext
    state: SessionContextState
