"""Configuration-driven runtime factory for RoboAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.graph.state import CompiledStateGraph

from roboagent.agent.builder import AgentBuilder
from roboagent.agent.features import RuntimeFeatures
from roboagent.config import AppConfig
from roboagent.middleware import build_runtime_middlewares
from roboagent.model.factory import create_chat_model
from roboagent.runtime import MemoryRunEventStore, RunEventStore, RunManager, RunRecord
from roboagent.runtime.runs import RunStatus
from roboagent.tool import ToolManager


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Explicit per-runtime inputs for config-driven assembly."""

    agent_id: str = "roboagent"
    model_name: str | None = None
    system_prompt: str | SystemMessage | None = None
    name: str | None = None
    thread_id: str = "default"
    run_id: str | None = None
    model_overrides: dict[str, Any] = field(default_factory=dict)
    extra_middlewares: list[AgentMiddleware] | None = None
    event_store: RunEventStore | None = None
    run_manager: RunManager | None = None


def create_roboagent_runtime(
    app_config: AppConfig,
    *,
    runtime_context: RuntimeContext | None = None,
    features: RuntimeFeatures | None = None,
) -> CompiledStateGraph:
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

    tool_manager = ToolManager() if enabled_features.tool_resolution else None
    run_record, event_store = _create_run_record(context, enabled_features)
    middlewares = build_runtime_middlewares(
        enabled_features,
        skills=active_skills,
        thread_id=context.thread_id,
        run_id=run_record.run_id if run_record is not None else context.run_id or "default",
        event_store=event_store,
        run_manager=context.run_manager,
        extra_middlewares=context.extra_middlewares,
    )

    return AgentBuilder(
        model=model,
        system_prompt=context.system_prompt,
        middlewares=middlewares,
        name=context.name,
        agent_id=context.agent_id,
        skill_manager=skill_manager,
        tool_manager=tool_manager,
    ).build()


def _create_run_record(
    context: RuntimeContext,
    features: RuntimeFeatures,
) -> tuple[RunRecord | None, RunEventStore | None]:
    """Create a run record and event store when run journaling is enabled."""
    if not features.run_journal:
        return None, None

    manager = context.run_manager or RunManager()
    event_store = context.event_store or MemoryRunEventStore()
    record = manager.create(
        thread_id=context.thread_id,
        assistant_id=context.name,
        run_id=context.run_id,
    )
    manager.set_status(record.run_id, RunStatus.RUNNING)
    return record, event_store


__all__ = ["RuntimeContext", "create_roboagent_runtime"]
