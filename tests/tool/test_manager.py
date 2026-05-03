from __future__ import annotations

import unittest

from roboagent.tool import ResolutionContext, ToolManager, ToolSpec
from roboagent.tool.errors import ToolRegistrationError

from tests.tool._helpers import create_structured_tool


class ToolManagerTests(unittest.TestCase):
    def test_register_single_tool(self) -> None:
        manager = ToolManager()
        base_tool = create_structured_tool("map.read")
        spec = ToolSpec(
            name="map.read",
            description="Read map data.",
            group="map",
            source="builtin",
        )

        registered = manager.register(base_tool, spec)

        self.assertEqual(registered.name, "map.read")
        self.assertEqual([tool.name for tool in manager.list_tools()], ["map.read"])

    def test_register_batch_tools(self) -> None:
        manager = ToolManager()

        registered = manager.register_batch(
            [
                (
                    create_structured_tool("map.read"),
                    ToolSpec(
                        name="map.read",
                        description="Read map data.",
                        group="map",
                        source="builtin",
                    ),
                ),
                (
                    create_structured_tool("pose.read"),
                    ToolSpec(
                        name="pose.read",
                        description="Read pose data.",
                        group="pose",
                        source="builtin",
                        deferred=True,
                    ),
                ),
            ]
        )

        self.assertEqual([tool.name for tool in registered], ["map.read", "pose.read"])
        self.assertEqual([tool.name for tool in manager.list_tools()], ["map.read", "pose.read"])

    def test_register_single_tool_requires_spec(self) -> None:
        manager = ToolManager()

        with self.assertRaises(ToolRegistrationError):
            manager.register(create_structured_tool("map.read"))

    def test_resolve_tools_and_get_tools_return_direct_tools_only(self) -> None:
        manager = ToolManager()
        manager.register_batch(
            [
                (
                    create_structured_tool("map.read"),
                    ToolSpec(
                        name="map.read",
                        description="Read map data.",
                        group="map",
                        source="builtin",
                    ),
                ),
                (
                    create_structured_tool("map.search"),
                    ToolSpec(
                        name="map.search",
                        description="Search map data.",
                        group="map",
                        source="builtin",
                        deferred=True,
                    ),
                ),
            ]
        )

        context = ResolutionContext(agent_id="lead")
        resolved = manager.resolve_tools(context)

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["map.read"])
        self.assertEqual([tool.name for tool in resolved.deferred_tools], ["map.search"])
        self.assertEqual([tool.name for tool in manager.get_tools(context)], ["map.read"])

    def test_resolution_context_uses_activated_skill_allowed_tools(self) -> None:
        manager = ToolManager()
        manager.register_batch(
            [
                (
                    create_structured_tool("map.read"),
                    ToolSpec(
                        name="map.read",
                        description="Read map data.",
                        group="map",
                        source="builtin",
                    ),
                ),
                (
                    create_structured_tool("pose.read"),
                    ToolSpec(
                        name="pose.read",
                        description="Read pose data.",
                        group="pose",
                        source="builtin",
                    ),
                ),
            ]
        )
        skill = type("SkillStub", (), {"allowed_tools": ("pose.read",)})()
        context = ResolutionContext(agent_id="lead", activated_skills=(skill,))

        resolved = manager.resolve_tools(context)

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["pose.read"])

    def test_resolution_context_keeps_subagent_within_parent_allowlist(self) -> None:
        manager = ToolManager()
        manager.register_batch(
            [
                (
                    create_structured_tool("map.read"),
                    ToolSpec(
                        name="map.read",
                        description="Read map data.",
                        group="map",
                        source="builtin",
                    ),
                ),
                (
                    create_structured_tool("pose.read"),
                    ToolSpec(
                        name="pose.read",
                        description="Read pose data.",
                        group="pose",
                        source="builtin",
                    ),
                ),
            ]
        )
        context = ResolutionContext(
            agent_id="lead",
            subagent_id="planner",
            activated_allowed_tools=("map.read", "pose.read"),
            parent_allowed_tools=("map.read",),
        )

        resolved = manager.resolve_tools(context)

        self.assertEqual([tool.name for tool in resolved.direct_tools], ["map.read"])


if __name__ == "__main__":
    unittest.main()
