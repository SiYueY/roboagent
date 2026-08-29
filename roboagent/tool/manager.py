"""Public facade for the RoboAgent tool subsystem."""

from __future__ import annotations

from collections.abc import Iterable

from roboagent.tool.registry import ToolRegistry
from roboagent.tool.resolver import ResolutionContext, ResolvedToolSet, ToolResolver
from roboagent.tool.tool import Tool


class ToolManager:
    """Unified public interface for the tool subsystem."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        resolver: ToolResolver | None = None,
    ) -> None:
        self._registry = registry or ToolRegistry()
        self._resolver = resolver or ToolResolver()

    @property
    def registry(self) -> ToolRegistry:
        """Expose the underlying registry for advanced integrations."""
        return self._registry

    def register(
        self,
        tool: Tool,
        /,
    ) -> Tool:
        """Register one tool.

        Args:
            tool: Native runtime tool instance.
            spec: Metadata schema for single-tool registration.

        Returns:
            Registered runtime `Tool`.

        Raises:
            ToolRegistrationError: If the single-tool form omits `spec`.
        """
        return self._registry.register(tool)

    def register_batch(self, tools: Iterable[Tool], /) -> list[Tool]:
        """Register a batch of tools.

        Args:
            items: Iterable of `(base_tool, spec)` tuples.

        Returns:
            Registered runtime tools in input order.
        """
        return self._registry.register_batch(list(tools))

    def list_tools(self, *, source: str | None = None, group: str | None = None) -> list[Tool]:
        """List registered tools with optional filtering.

        Args:
            source: Optional source filter.
            group: Optional group filter.

        Returns:
            Matching registered runtime tools.
        """
        return self._registry.list_all(source=source, group=group)

    def resolve_tools(
        self,
        context: ResolutionContext,
    ) -> ResolvedToolSet:
        """Resolve direct and deferred tools for the provided context.

        Args:
            context: Resolution context for the current agent or sub-agent.

        Returns:
            The resolved direct/deferred tool buckets.
        """
        tools = self._registry.list_all()
        return self._resolver.resolve(
            tools,
            context,
        )

    def get_tools(
        self,
        context: ResolutionContext,
    ) -> list[Tool]:
        """Return directly visible native tools for the provided context.

        Args:
            context: Resolution context for the current agent or sub-agent.

        Returns:
            Directly visible native tools.
        """
        resolved = self.resolve_tools(context)
        return resolved.direct_tools


__all__ = ["ToolManager"]
