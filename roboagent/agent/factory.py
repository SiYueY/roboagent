"""Configuration-driven runtime factory for RoboAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from roboagent.agent.builder import AgentBuilder
from roboagent.agent.agent import Agent
from roboagent.agent.hooks import ContextTransform
from roboagent.agent.features import RuntimeFeatures
from roboagent.config import AppConfig
from roboagent.model.factory import create_chat_model
from roboagent.runtime import MemoryRunEventStore, RunEventStore, RunManager, RunRecord
from roboagent.runtime.runs import RunStatus
from roboagent.tool import Tool, ToolManager


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Explicit per-runtime inputs for config-driven assembly."""

    agent_id: str = "roboagent"
    model_name: str | None = None
    system_prompt: str | None = None
    name: str | None = None
    thread_id: str = "default"
    run_id: str | None = None
    model_overrides: dict[str, Any] = field(default_factory=dict)
    tools: tuple[Tool, ...] = ()
    tool_manager: ToolManager | None = None
    context_transforms: tuple[ContextTransform, ...] = ()
    event_store: RunEventStore | None = None
    run_manager: RunManager | None = None


def create_roboagent_runtime(
    app_config: AppConfig,
    *,
    runtime_context: RuntimeContext | None = None,
    features: RuntimeFeatures | None = None,
) -> Agent:
    """Create a RoboAgent runtime from an already-loaded AppConfig."""
    context = runtime_context or RuntimeContext()
    enabled_features = features or RuntimeFeatures()

    registry = app_config.to_model_registry()
    model = create_chat_model(
        context.model_name,
        registry=registry,
        **context.model_overrides,
    )

    skill_manager = None
    active_skills = []
    if enabled_features.skill_context:
        skill_manager = app_config.create_skill_manager()
        skill_manager.load(clear=True)
        active_skills = skill_manager.list_skills(enabled_only=True)

    run_record, event_store, run_manager = _create_run_record(context, enabled_features)
    transforms = list(context.context_transforms)
    if enabled_features.skill_context:
        from roboagent.skill.context import create_skill_context_transform
        transforms.insert(0, create_skill_context_transform(active_skills))
    agent = AgentBuilder(
        model=model,
        tools=list(context.tools),
        system_prompt=context.system_prompt,
        context_transforms=transforms,
        name=context.name,
        agent_id=context.agent_id,
        skill_manager=skill_manager,
        tool_manager=context.tool_manager if enabled_features.tool_resolution else None,
    ).build()
    if enabled_features.run_journal:
        from roboagent.runtime.journal import RunJournalSubscriber
        agent.subscribe(RunJournalSubscriber(thread_id=context.thread_id, run_id=run_record.run_id, event_store=event_store, run_manager=run_manager))
    return agent


def _create_run_record(
    context: RuntimeContext,
    features: RuntimeFeatures,
) -> tuple[RunRecord | None, RunEventStore | None, RunManager | None]:
    """Create a run record and event store when run journaling is enabled."""
    if not features.run_journal:
        return None, None, None

    manager = context.run_manager or RunManager()
    event_store = context.event_store or MemoryRunEventStore()
    record = manager.create(
        thread_id=context.thread_id,
        assistant_id=context.name,
        run_id=context.run_id,
    )
    manager.set_status(record.run_id, RunStatus.RUNNING)
    return record, event_store, manager


__all__ = ["RuntimeContext", "create_roboagent_runtime"]
