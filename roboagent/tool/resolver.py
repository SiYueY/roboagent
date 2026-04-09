"""Context-aware resolution for managed tools."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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


class ToolResolver:
    """Resolve visible tools for an agent or subagent context."""

    def resolve(
        self,
        tools: Sequence[Tool],
        agent_id: str,
        *,
        subagent_id: str | None = None,
        activated_allowed_tools: Sequence[str] = (),
        parent_allowed_tools: Sequence[str] | None = None,
    ) -> ResolvedToolSet:
        """Resolve tools for the provided context.

        Args:
            tools: Candidate runtime tools.
            agent_id: Primary agent identifier.
            subagent_id: Optional subagent identifier.
            activated_allowed_tools: Allowlist derived from activated skills.
            parent_allowed_tools: Optional parent allowlist used to ensure
                subagents cannot expand capabilities.

        Returns:
            A resolved set of direct and deferred runtime tools.
        """
        principal_id = subagent_id or agent_id
        parent_allowed = set(parent_allowed_tools) if parent_allowed_tools is not None else None
        activated_allowed = set(activated_allowed_tools)

        direct_tools: list[Tool] = []
        deferred_tools: list[Tool] = []

        for tool in tools:
            if not tool.is_available_to(principal_id):
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


__all__ = ["ResolvedToolSet", "ToolResolver"]
