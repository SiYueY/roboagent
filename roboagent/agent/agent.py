"""Stable immutable Agent capability composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from roboagent.agent.hooks import RunHook
from roboagent.agent.types import RunConfig
from roboagent.context import ContextManager, FullContextManager, PromptInput
from roboagent.message import AgentMessage, MediaLimits
from roboagent.model import Model
from roboagent.tool import (
    ApprovalProvider,
    ApprovalSettings,
    AllowAllToolPolicy,
    ToolExecutionPolicy,
    ToolRegistry,
)

if TYPE_CHECKING:
    from roboagent.agent.delegation import ChildSessionFactory
    from roboagent.agent.session import Session
    from roboagent.skill import SkillManager
    from roboagent.tool import (
        ArtifactDestination,
        ArtifactReader,
        Tool,
        ToolResultMaterializer,
        Workspace,
    )
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
        if not callable(getattr(model, "stream", None)) or not hasattr(
            model, "capabilities"
        ):
            raise TypeError("Agent requires a canonical Model.")
        if tool_registry is not None and not isinstance(tool_registry, ToolRegistry):
            raise TypeError("tool_registry must be ToolRegistry or None.")
        if prompt is not None and not isinstance(prompt, PromptInput):
            raise TypeError("prompt must be PromptInput or None.")
        if context_manager is not None and not callable(
            getattr(context_manager, "prepare", None)
        ):
            raise TypeError("context_manager must implement prepare().")
        if tool_policy is not None and not callable(
            getattr(tool_policy, "evaluate", None)
        ):
            raise TypeError("tool_policy must implement evaluate().")
        if default_run_config is not None and not isinstance(
            default_run_config, RunConfig
        ):
            raise TypeError("default_run_config must be RunConfig or None.")
        if media_limits is not None and not isinstance(media_limits, MediaLimits):
            raise TypeError("media_limits must be MediaLimits or None.")
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self, "tool_registry", (tool_registry or ToolRegistry()).snapshot()._seal()
        )
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(
            self, "context_manager", context_manager or FullContextManager()
        )
        object.__setattr__(self, "hooks", tuple(hooks))
        object.__setattr__(self, "tool_policy", tool_policy or AllowAllToolPolicy())
        object.__setattr__(
            self, "default_run_config", default_run_config or RunConfig()
        )
        object.__setattr__(self, "media_limits", media_limits or MediaLimits())
        object.__setattr__(self, "skill_manager", skill_manager)
        if approval_provider is not None and not callable(
            getattr(approval_provider, "request", None)
        ):
            raise TypeError("approval_provider must implement request().")
        if approval_settings is not None and not isinstance(
            approval_settings, ApprovalSettings
        ):
            raise TypeError("approval_settings must be ApprovalSettings or None.")
        object.__setattr__(self, "approval_provider", approval_provider)
        object.__setattr__(
            self, "approval_settings", approval_settings or ApprovalSettings()
        )

    def new_session(
        self,
        messages: Sequence[AgentMessage] = (),
        *,
        session_id: str | None = None,
        workspace: "Workspace | None" = None,
        result_materializer: "ToolResultMaterializer | None" = None,
        repository: "SessionRepository | None" = None,
        allow_nondurable_artifacts: bool = False,
        artifact_reader: "ArtifactReader | None" = None,
        artifact_destination: "ArtifactDestination | None" = None,
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
            artifact_reader=artifact_reader,
            artifact_destination=artifact_destination,
        )

    def as_tool(
        self,
        *,
        name: str,
        description: str,
        session_factory: "ChildSessionFactory | None" = None,
        run_config: RunConfig | None = None,
    ) -> "Tool":
        """Expose this immutable Agent as one composite Tool capability."""
        from roboagent.message import (
            ArtifactReferenceContent,
            FrozenJsonObject,
            JsonContent,
            TextContent,
        )
        from roboagent.runtime import ExecutionRequestError, RunStatus
        from roboagent.tool import (
            CompositeToolOutcome,
            Tool,
            ToolDefinition,
            ToolEffectKind,
            ToolEffectReporting,
            ToolErrorInfo,
            ToolExecutionFailure,
            ToolJsonContent,
            ToolContent,
            ToolTextContent,
        )

        if session_factory is not None and not callable(
            getattr(session_factory, "create", None)
        ):
            raise TypeError("session_factory must implement create().")
        if run_config is not None and not isinstance(run_config, RunConfig):
            raise TypeError("run_config must be RunConfig or None.")
        schema = FrozenJsonObject(
            {
                "type": "object",
                "properties": {"task": {"type": "string", "minLength": 1}},
                "required": ["task"],
                "additionalProperties": False,
            }
        )
        effect_kind = ToolEffectKind.READ_ONLY
        for definition in self.tool_registry.definitions():
            registered = self.tool_registry.get(definition.name)
            if (
                registered is not None
                and registered.effect_kind is ToolEffectKind.SIDE_EFFECTING
            ):
                effect_kind = ToolEffectKind.SIDE_EFFECTING
                break

        async def invoke(arguments, context):
            task = arguments["task"]
            if not isinstance(task, str) or not task.strip():
                raise ToolExecutionFailure(
                    ToolErrorInfo("invalid_arguments", "Agent task must not be blank.")
                )
            if context.execution is None:
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "nested_execution_unavailable",
                        "Nested execution is unavailable.",
                    )
                )
            try:
                child = await context.execution.run_child_agent(
                    self,
                    task,
                    session_factory=session_factory,
                    run_config=run_config,
                )
            except ExecutionRequestError as exc:
                raise ToolExecutionFailure(ToolErrorInfo(exc.code, str(exc))) from exc
            except Exception as exc:
                code = getattr(exc, "code", "child_execution_failed")
                raise ToolExecutionFailure(
                    ToolErrorInfo(code, "Child Agent execution failed.")
                ) from exc
            if child.status is RunStatus.CANCELLED:
                if context.cancellation.cancelled:
                    raise asyncio.CancelledError()
                raise ToolExecutionFailure(
                    ToolErrorInfo("child_cancelled", "Child Agent was cancelled.")
                )
            if child.status is RunStatus.FAILED:
                child_code = child.error.code if child.error is not None else None
                if child_code == "cleanup_error":
                    code = "child_cleanup_failed"
                elif child_code in {
                    "child_artifact_too_large",
                    "child_artifact_digest_mismatch",
                    "child_output_materialization_failed",
                }:
                    code = child_code
                else:
                    code = "child_execution_failed"
                raise ToolExecutionFailure(
                    ToolErrorInfo(code, "Child Agent execution failed.")
                )
            if child.output is None:
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "child_output_missing", "Child Agent returned no output."
                    )
                )
            content: list[ToolContent] = []
            for item in child.output.content:
                if isinstance(item, TextContent):
                    content.append(ToolTextContent(item.text))
                elif isinstance(item, JsonContent):
                    content.append(ToolJsonContent(item.value))
                elif isinstance(item, ArtifactReferenceContent):
                    content.append(item)
                else:
                    raise ToolExecutionFailure(
                        ToolErrorInfo(
                            "child_output_materialization_failed",
                            "Child output was not promoted.",
                        )
                    )
            return CompositeToolOutcome(tuple(content))

        return Tool(
            ToolDefinition(name, description, schema),
            invoke,
            effect_kind=effect_kind,
            effect_reporting=ToolEffectReporting.COMPOSITE,
        )
