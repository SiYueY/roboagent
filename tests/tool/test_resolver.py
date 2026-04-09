from __future__ import annotations

import unittest

from roboagent.tool import Tool, ToolResolver, ToolSpec

from tests.tool._helpers import create_structured_tool


def build_tool(
    name: str,
    *,
    visible_by_default: bool = True,
    deferred: bool = False,
    allowed_agents: tuple[str, ...] = (),
) -> Tool:
    """Build a runtime tool for resolver tests."""
    return Tool.from_spec(
        create_structured_tool(name),
        ToolSpec(
            name=name,
            description=f"Tool {name}.",
            group="default",
            source="builtin",
            visible_by_default=visible_by_default,
            deferred=deferred,
            allowed_agents=allowed_agents,
        ),
    )


class ToolResolverTests(unittest.TestCase):
    def test_resolve_splits_direct_and_deferred_tools(self) -> None:
        resolver = ToolResolver()
        tools = [
            build_tool("map.read"),
            build_tool("map.search", deferred=True),
            build_tool("pose.read", visible_by_default=False),
        ]

        resolved = resolver.resolve(tools, "lead")

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["map.read"])
        self.assertEqual(
            [tool.name for tool in resolved.deferred_tools],
            ["map.search", "pose.read"],
        )

    def test_resolve_applies_activated_allowed_tools(self) -> None:
        resolver = ToolResolver()
        tools = [build_tool("map.read"), build_tool("pose.read")]

        resolved = resolver.resolve(tools, "lead", activated_allowed_tools=("pose.read",))

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["pose.read"])

    def test_subagent_cannot_expand_parent_tool_set(self) -> None:
        resolver = ToolResolver()
        tools = [
            build_tool("map.read", allowed_agents=("worker",)),
            build_tool("pose.read", allowed_agents=("worker",)),
        ]

        resolved = resolver.resolve(
            tools,
            "lead",
            subagent_id="worker",
            parent_allowed_tools=("map.read",),
        )

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["map.read"])

    def test_allowed_agents_filter_is_applied_to_principal(self) -> None:
        resolver = ToolResolver()
        tools = [
            build_tool("map.read", allowed_agents=("lead",)),
            build_tool("pose.read", allowed_agents=("worker",)),
        ]

        resolved = resolver.resolve(tools, "lead")

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["map.read"])


if __name__ == "__main__":
    unittest.main()
