"""In-memory storage for managed tools."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from roboagent.tool.errors import DuplicateToolError, ToolNotFoundError, ToolRegistrationError
from roboagent.tool.tool import Tool


class ToolRegistry:
    """In-memory registry for managed tools."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            self.register_batch(tools)

    def register(self, tool: Tool, /) -> Tool:
        """Register one runtime tool.

        Args:
            tool: Runtime tool to register.

        Returns:
            The registered tool.

        Raises:
            ToolRegistrationError: If the runtime tool name does not match the
                underlying LangChain tool name.
            DuplicateToolError: If the tool name is already registered.
        """
        base_tool_name = getattr(tool.base_tool, "name", None)
        if base_tool_name != tool.name:
            raise ToolRegistrationError(
                f"Tool '{tool.name}' must match BaseTool name '{base_tool_name}'."
            )
        if tool.name in self._tools:
            raise DuplicateToolError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        return tool

    def register_batch(self, tools: Iterable[Tool], /) -> list[Tool]:
        """Register multiple tools in order.

        Args:
            tools: Runtime tools to register.

        Returns:
            Registered tools in input order.

        Raises:
            DuplicateToolError: If duplicate names are present in the batch or
                already registered.
        """
        tool_list = list(tools)
        names = [tool.name for tool in tool_list]
        duplicate_names = {name for name, count in Counter(names).items() if count > 1}
        if duplicate_names:
            duplicates = ", ".join(sorted(duplicate_names))
            raise DuplicateToolError(
                f"Duplicate tool names in batch registration: {duplicates}"
            )
        existing_names = sorted(name for name in names if name in self._tools)
        if existing_names:
            duplicates = ", ".join(existing_names)
            raise DuplicateToolError(f"Tool names are already registered: {duplicates}")

        registered: list[Tool] = []
        for tool in tool_list:
            registered.append(self.register(tool))
        return registered

    def get(self, name: str) -> Tool | None:
        """Return a tool by name if present."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._tools

    def require(self, name: str) -> Tool:
        """Return a tool by name or raise.

        Args:
            name: Registered tool name.

        Returns:
            The matching runtime tool.

        Raises:
            ToolNotFoundError: If the tool is not registered.
        """
        tool = self.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        return tool

    def list_all(self, *, source: str | None = None, group: str | None = None) -> list[Tool]:
        """Return registered tools with optional filtering.

        Args:
            source: Optional source filter.
            group: Optional group filter.

        Returns:
            Name-sorted matching runtime tools.
        """
        tools = list(self._tools.values())
        if source is not None:
            tools = [tool for tool in tools if tool.source == source]
        if group is not None:
            tools = [tool for tool in tools if tool.group == group]
        tools.sort(key=lambda tool: tool.name)
        return tools

    def count(self) -> int:
        """Return the total number of registered tools."""
        return len(self._tools)

    def clear(self) -> None:
        """Remove all registered tools."""
        self._tools.clear()


__all__ = ["ToolRegistry"]
