"""Immutable Agent definition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from roboagent.agent.types import AgentHooks
from roboagent.context import ContextManager, FullContextManager
from roboagent.model.client import ChatModel
from roboagent.runtime import Message
from roboagent.tool import Tool


@dataclass(frozen=True, slots=True)
class Agent:
    """Reusable, immutable definition of a model-backed agent."""

    model: ChatModel
    tools: tuple[Tool, ...]
    system_prompt: str | None
    hooks: AgentHooks
    context_manager: ContextManager
    max_turns: int
    run_timeout: float | None

    def __init__(
        self,
        model: ChatModel,
        *,
        tools: Sequence[Tool] = (),
        system_prompt: str | None = None,
        hooks: AgentHooks | None = None,
        context_manager: ContextManager | None = None,
        max_turns: int = 32,
        run_timeout: float | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive.")
        if run_timeout is not None and run_timeout <= 0:
            raise ValueError("run_timeout must be positive.")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "tools", tuple(tools))
        object.__setattr__(self, "system_prompt", system_prompt)
        object.__setattr__(self, "hooks", hooks or AgentHooks())
        object.__setattr__(self, "context_manager", context_manager or FullContextManager())
        object.__setattr__(self, "max_turns", max_turns)
        object.__setattr__(self, "run_timeout", run_timeout)

    def new_session(
        self,
        messages: Sequence[Message] = (),
        *,
        session_id: str | None = None,
    ) -> "AgentSession":
        from roboagent.agent.session import AgentSession

        return AgentSession(self, messages, session_id)
