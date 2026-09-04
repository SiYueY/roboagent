"""Immutable reusable V1 Agent definition."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from roboagent.agent.types import RunConfig
from roboagent.agent.hooks import AgentHooks
from roboagent.context.manager import ContextManager, FullContextManager
from roboagent.message import MediaLimits, Message
from roboagent.model.client import ChatModel
from roboagent.runtime.types import MediaResolver
from roboagent.tool.tool import Tool
from roboagent.tool.resolver import StaticToolResolver, ToolResolver

if TYPE_CHECKING:
    from roboagent.agent.session import AgentSession


@dataclass(frozen=True, slots=True)
class Agent:
    model: ChatModel
    tools: tuple[Tool, ...]
    system_prompt: str | None
    context_manager: ContextManager
    default_run_config: RunConfig
    media_limits: MediaLimits
    media_resolver: MediaResolver | None = None
    tool_resolver: ToolResolver = field(default_factory=StaticToolResolver)
    hooks: AgentHooks | None = None

    def __init__(
        self,
        model: ChatModel,
        *,
        tools: Sequence[Tool] = (),
        system_prompt: str | None = None,
        context_manager: ContextManager | None = None,
        hooks: AgentHooks | None = None,
        default_run_config: RunConfig = RunConfig(),
        media_limits: MediaLimits = MediaLimits(),
        media_resolver: MediaResolver | None = None,
        tool_resolver: ToolResolver | None = None,
    ) -> None:
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "tools", tuple(tools))
        object.__setattr__(self, "system_prompt", system_prompt)
        object.__setattr__(
            self, "context_manager", context_manager or FullContextManager()
        )
        object.__setattr__(self, "default_run_config", default_run_config)
        object.__setattr__(self, "media_limits", media_limits)
        object.__setattr__(self, "media_resolver", media_resolver)
        object.__setattr__(self, "tool_resolver", tool_resolver or StaticToolResolver())
        object.__setattr__(self, "hooks", hooks)

    def new_session(
        self, messages: Sequence[Message] = (), *, session_id: str | None = None
    ) -> "AgentSession":
        from roboagent.agent.session import AgentSession

        return AgentSession(self, messages, session_id)
