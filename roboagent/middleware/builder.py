"""Middleware assembly helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from roboagent.middleware.run_journal import RunJournalMiddleware
from roboagent.middleware.skill_context import SkillContextMiddleware
from roboagent.middleware.tool_error import ToolErrorHandlingMiddleware
from roboagent.runtime.events import RunEventStore
from roboagent.runtime.runs import RunManager
from roboagent.skill import Skill


def build_runtime_middlewares(
    features: Any,
    *,
    skills: Sequence[Skill] = (),
    thread_id: str = "default",
    run_id: str = "default",
    event_store: RunEventStore | None = None,
    run_manager: RunManager | None = None,
    extra_middlewares: Sequence[AgentMiddleware] | None = None,
) -> list[AgentMiddleware]:
    """Build the default middleware chain for a RoboAgent runtime."""
    middlewares: list[AgentMiddleware] = []

    if features.skill_context:
        middlewares.append(SkillContextMiddleware(skills))

    if features.run_journal:
        middlewares.append(
            RunJournalMiddleware(
                thread_id=thread_id,
                run_id=run_id,
                event_store=event_store,
                run_manager=run_manager,
            )
        )

    middlewares.append(ToolErrorHandlingMiddleware())

    if extra_middlewares:
        middlewares.extend(extra_middlewares)

    return middlewares


__all__ = ["build_runtime_middlewares"]
