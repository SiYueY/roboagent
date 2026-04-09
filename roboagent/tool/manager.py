"""Public facade for the RoboAgent tool subsystem."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import cast, overload

from langchain_core.tools import BaseTool as LangChainBaseTool

from roboagent.tool.errors import ToolRegistrationError
from roboagent.tool.registry import ToolRegistry
from roboagent.tool.resolver import ResolvedToolSet, ToolResolver
from roboagent.tool.schema import ToolSpec
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

    @overload
    def register(self, base_tool: LangChainBaseTool, spec: ToolSpec, /) -> Tool:
        ...

    @overload
    def register(self, items: Iterable[tuple[LangChainBaseTool, ToolSpec]], /) -> list[Tool]:
        ...

    def register(
        self,
        base_tool_or_items: LangChainBaseTool | Iterable[tuple[LangChainBaseTool, ToolSpec]],
        spec: ToolSpec | None = None,
        /,
    ) -> Tool | list[Tool]:
        """Register one tool or a batch of tools.

        Args:
            base_tool_or_items: Either one LangChain tool, or an iterable of
                `(base_tool, spec)` tuples.
            spec: Metadata schema for single-tool registration.

        Returns:
            A runtime `Tool` for single registration, or a list of runtime
            tools for batch registration.

        Raises:
            ToolRegistrationError: If the single-tool form omits `spec`.
        """
        if spec is not None:
            base_tool = cast("LangChainBaseTool", base_tool_or_items)
            tool = Tool.from_spec(base_tool, spec)
            return self._registry.register(tool)

        if isinstance(base_tool_or_items, LangChainBaseTool):
            raise ToolRegistrationError("register(base_tool, spec) requires a ToolSpec argument.")

        items = list(cast("Iterable[tuple[LangChainBaseTool, ToolSpec]]", base_tool_or_items))
        tools = [Tool.from_spec(base_tool, item_spec) for base_tool, item_spec in items]
        return self._registry.register(tools)

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
        agent_id: str,
        *,
        subagent_id: str | None = None,
        activated_allowed_tools: Sequence[str] = (),
        parent_allowed_tools: Sequence[str] | None = None,
    ) -> ResolvedToolSet:
        """Resolve direct and deferred tools for the provided context.

        Args:
            agent_id: Primary agent identifier.
            subagent_id: Optional subagent identifier.
            activated_allowed_tools: Optional allowlist from activated skills.
            parent_allowed_tools: Optional parent allowlist for subagent
                resolution.

        Returns:
            The resolved direct/deferred tool buckets.
        """
        tools = self._registry.list_all()
        return self._resolver.resolve(
            tools,
            agent_id,
            subagent_id=subagent_id,
            activated_allowed_tools=activated_allowed_tools,
            parent_allowed_tools=parent_allowed_tools,
        )

    def get_tools(
        self,
        agent_id: str,
        *,
        subagent_id: str | None = None,
        activated_allowed_tools: Sequence[str] = (),
        parent_allowed_tools: Sequence[str] | None = None,
    ) -> list[LangChainBaseTool]:
        """Return directly visible LangChain tools for the provided context.

        Args:
            agent_id: Primary agent identifier.
            subagent_id: Optional subagent identifier.
            activated_allowed_tools: Optional allowlist from activated skills.
            parent_allowed_tools: Optional parent allowlist for subagent
                resolution.

        Returns:
            Directly visible LangChain `BaseTool` instances.
        """
        resolved = self.resolve_tools(
            agent_id,
            subagent_id=subagent_id,
            activated_allowed_tools=activated_allowed_tools,
            parent_allowed_tools=parent_allowed_tools,
        )
        return [tool.base_tool for tool in resolved.direct_tools]


__all__ = ["ToolManager"]
