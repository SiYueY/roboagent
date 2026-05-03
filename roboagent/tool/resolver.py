"""Context-aware resolution for managed tools."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from roboagent.tool.tool import Tool


@dataclass(frozen=True, slots=True)
class ResolvedToolSet:
    """Resolved tool buckets for one agent context.

    Attributes:
        direct_tools: Tools that should be directly bound to the model.
        deferred_tools: Tools that are available but hidden from direct
            binding.
    """

    direct_tools: list[Tool]
    deferred_tools: list[Tool]


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Inputs used to resolve tool visibility for one runtime context."""

    agent_id: str
    subagent_id: str | None = None
    activated_allowed_tools: tuple[str, ...] = ()
    activated_skills: tuple[Any, ...] = field(default_factory=tuple)
    parent_allowed_tools: tuple[str, ...] | None = None

    @property
    def principal_id(self) -> str:
        """Return the active agent or sub-agent identifier."""
        return self.subagent_id or self.agent_id

    @property
    def effective_activated_allowed_tools(self) -> tuple[str, ...]:
        """Return allowlisted tools from explicit context and activated skills."""
        allowed: list[str] = list(self.activated_allowed_tools)
        for skill in self.activated_skills:
            allowed.extend(getattr(skill, "allowed_tools", ()) or ())
        return tuple(dict.fromkeys(allowed))


class ToolResolver:
    """Resolve visible tools for an agent or subagent context."""

    def resolve(
        self,
        tools: Sequence[Tool],
        context: ResolutionContext,
    ) -> ResolvedToolSet:
        """Resolve tools for the provided context.

        Args:
            tools: Candidate runtime tools.
            context: Resolution context for the current agent or sub-agent.

        Returns:
            A resolved set of direct and deferred runtime tools.
        """
        parent_allowed = (
            set(context.parent_allowed_tools)
            if context.parent_allowed_tools is not None
            else None
        )
        activated_allowed = set(context.effective_activated_allowed_tools)

        direct_tools: list[Tool] = []
        deferred_tools: list[Tool] = []

        for tool in tools:
            if not tool.is_available_to(context.principal_id):
                continue
            if parent_allowed is not None and tool.name not in parent_allowed:
                continue
            if activated_allowed and tool.name not in activated_allowed:
                continue

            if not tool.is_directly_visible():
                deferred_tools.append(tool)
            else:
                direct_tools.append(tool)

        return ResolvedToolSet(direct_tools=direct_tools, deferred_tools=deferred_tools)


__all__ = ["ResolutionContext", "ResolvedToolSet", "ToolResolver"]
