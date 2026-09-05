"""Stable immutable Agent capability composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from roboagent.agent.hooks import RunHook
from roboagent.agent.types import RunConfig
from roboagent.context import ContextManager, FullContextManager, PromptInput
from roboagent.message import AgentMessage, MediaLimits
from roboagent.model import Model
from roboagent.tool import ApprovalProvider, ApprovalSettings, AllowAllToolPolicy, ToolExecutionPolicy, ToolRegistry

if TYPE_CHECKING:
    from roboagent.agent.session import Session
    from roboagent.skill import SkillManager
    from roboagent.tool import ToolResultMaterializer, Workspace
    from roboagent.agent.persistence import SessionRepository


@dataclass(frozen=True, slots=True)
class Agent:
    model: Model
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    prompt: PromptInput | None = None
    context_manager: ContextManager = field(default_factory=FullContextManager)
    hooks: tuple[RunHook, ...] = ()
    tool_policy: ToolExecutionPolicy = field(default_factory=AllowAllToolPolicy)
    default_run_config: RunConfig = field(default_factory=RunConfig)
    media_limits: MediaLimits = field(default_factory=MediaLimits)
    skill_manager: "SkillManager | None" = None
    approval_provider: ApprovalProvider | None = None
    approval_settings: ApprovalSettings = field(default_factory=ApprovalSettings)

    def __init__(
        self,
        model: Model,
        *,
        tool_registry: ToolRegistry | None = None,
        prompt: PromptInput | None = None,
        context_manager: ContextManager | None = None,
        hooks: Sequence[RunHook] = (),
        tool_policy: ToolExecutionPolicy | None = None,
        default_run_config: RunConfig | None = None,
        media_limits: MediaLimits | None = None,
        skill_manager: "SkillManager | None" = None,
        approval_provider: ApprovalProvider | None = None,
        approval_settings: ApprovalSettings | None = None,
    ) -> None:
        if not callable(getattr(model, "stream", None)) or not hasattr(model, "capabilities"):
            raise TypeError("Agent requires a canonical Model.")
        if tool_registry is not None and not isinstance(tool_registry, ToolRegistry):
            raise TypeError("tool_registry must be ToolRegistry or None.")
        if prompt is not None and not isinstance(prompt, PromptInput):
            raise TypeError("prompt must be PromptInput or None.")
        if context_manager is not None and not callable(getattr(context_manager, "prepare", None)):
            raise TypeError("context_manager must implement prepare().")
        if tool_policy is not None and not callable(getattr(tool_policy, "evaluate", None)):
            raise TypeError("tool_policy must implement evaluate().")
        if default_run_config is not None and not isinstance(default_run_config, RunConfig):
            raise TypeError("default_run_config must be RunConfig or None.")
        if media_limits is not None and not isinstance(media_limits, MediaLimits):
            raise TypeError("media_limits must be MediaLimits or None.")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "tool_registry", (tool_registry or ToolRegistry()).snapshot()._seal())
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "context_manager", context_manager or FullContextManager())
        object.__setattr__(self, "hooks", tuple(hooks))
        object.__setattr__(self, "tool_policy", tool_policy or AllowAllToolPolicy())
        object.__setattr__(self, "default_run_config", default_run_config or RunConfig())
        object.__setattr__(self, "media_limits", media_limits or MediaLimits())
        object.__setattr__(self, "skill_manager", skill_manager)
        if approval_provider is not None and not callable(getattr(approval_provider, "request", None)):
            raise TypeError("approval_provider must implement request().")
        if approval_settings is not None and not isinstance(approval_settings, ApprovalSettings):
            raise TypeError("approval_settings must be ApprovalSettings or None.")
        object.__setattr__(self, "approval_provider", approval_provider)
        object.__setattr__(self, "approval_settings", approval_settings or ApprovalSettings())

    def new_session(
        self,
        messages: Sequence[AgentMessage] = (),
        *,
        session_id: str | None = None,
        workspace: "Workspace | None" = None,
        result_materializer: "ToolResultMaterializer | None" = None,
        repository: "SessionRepository | None" = None,
        allow_nondurable_artifacts: bool = False,
    ) -> "Session":
        from roboagent.agent.session import Session

        return Session(
            self,
            messages,
            session_id,
            workspace=workspace,
            result_materializer=result_materializer,
            repository=repository,
            allow_nondurable_artifacts=allow_nondurable_artifacts,
        )
