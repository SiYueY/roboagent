"""Runtime tool value object."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool as LangChainBaseTool

from roboagent.tool.errors import ToolRegistrationError
from roboagent.tool.schema import ToolSpec


@dataclass(frozen=True, slots=True)
class Tool:
    """Runtime representation of one managed tool.

    Attributes:
        base_tool: Backing LangChain tool instance.
        name: Unique tool identifier.
        description: Human-readable summary for operators and the model.
        group: Logical grouping used for filtering.
        source: Logical source label such as `builtin` or `project`.
        visible_by_default: Whether the tool is directly bound without discovery.
        deferred: Whether the tool should be hidden from direct binding.
        allowed_agents: Optional allowlist of agent or subagent identifiers.
    """

    base_tool: LangChainBaseTool
    name: str
    description: str
    group: str
    source: str
    visible_by_default: bool = True
    deferred: bool = False
    allowed_agents: tuple[str, ...] = ()

    @classmethod
    def from_spec(cls, base_tool: LangChainBaseTool, spec: ToolSpec) -> Tool:
        """Build a runtime tool from a LangChain tool and validated spec.

        Args:
            base_tool: Backing LangChain tool instance.
            spec: Validated metadata schema.

        Returns:
            A runtime `Tool` instance.

        Raises:
            ToolRegistrationError: If the LangChain tool is missing a valid
                name, or if its name does not match the supplied schema.
        """
        tool_name = getattr(base_tool, "name", None)
        if not isinstance(tool_name, str) or not tool_name:
            raise ToolRegistrationError("BaseTool must define a non-empty 'name'.")
        if tool_name != spec.name:
            raise ToolRegistrationError(
                f"Tool spec name '{spec.name}' must match BaseTool name '{tool_name}'."
            )

        return cls(
            base_tool=base_tool,
            name=spec.name,
            description=spec.description,
            group=spec.group,
            source=spec.source,
            visible_by_default=spec.visible_by_default,
            deferred=spec.deferred,
            allowed_agents=spec.allowed_agents,
        )

    def is_available_to(self, principal_id: str) -> bool:
        """Return whether the tool is available to the given principal.

        Args:
            principal_id: Agent or subagent identifier.

        Returns:
            `True` if the tool is unrestricted or explicitly allows the
            provided principal.
        """
        return not self.allowed_agents or principal_id in self.allowed_agents

    def is_directly_visible(self) -> bool:
        """Return whether the tool should be directly bound to the model."""
        return self.visible_by_default and not self.deferred


__all__ = ["Tool"]
