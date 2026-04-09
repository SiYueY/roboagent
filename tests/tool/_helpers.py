"""Shared helpers for tool subsystem tests."""

from __future__ import annotations

from langchain_core.tools import StructuredTool


def create_structured_tool(name: str, description: str | None = None) -> StructuredTool:
    """Create a simple LangChain tool for tests.

    Args:
        name: Tool name exposed to the manager and registry.
        description: Optional description override.

    Returns:
        A `StructuredTool` instance with the requested name.
    """

    def _run(query: str) -> str:
        return f"{name}:{query}"

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=description or f"Test tool {name}.",
    )
