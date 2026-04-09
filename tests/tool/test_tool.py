from __future__ import annotations

import unittest

from roboagent.tool import Tool, ToolSpec
from roboagent.tool.errors import ToolRegistrationError

from tests.tool._helpers import create_structured_tool


class ToolTests(unittest.TestCase):
    def test_from_spec_copies_metadata_without_retaining_spec(self) -> None:
        base_tool = create_structured_tool("map.read", "Read map data.")
        spec = ToolSpec(
            name="map.read",
            description="Read map data.",
            group="map",
            source="builtin",
            allowed_agents=("lead",),
        )

        tool = Tool.from_spec(base_tool, spec)

        self.assertEqual(tool.base_tool, base_tool)
        self.assertEqual(tool.name, "map.read")
        self.assertEqual(tool.allowed_agents, ("lead",))
        self.assertFalse(hasattr(tool, "spec"))

    def test_from_spec_rejects_name_mismatch(self) -> None:
        base_tool = create_structured_tool("map.read")
        spec = ToolSpec(
            name="pose.read",
            description="Read pose data.",
            group="pose",
            source="builtin",
        )

        with self.assertRaises(ToolRegistrationError):
            Tool.from_spec(base_tool, spec)

    def test_visibility_helpers_reflect_tool_configuration(self) -> None:
        base_tool = create_structured_tool("map.read")
        hidden_tool = Tool(
            base_tool=base_tool,
            name="map.read",
            description="Read map data.",
            group="map",
            source="builtin",
            visible_by_default=False,
            deferred=False,
            allowed_agents=("lead",),
        )

        self.assertTrue(hidden_tool.is_available_to("lead"))
        self.assertFalse(hidden_tool.is_available_to("worker"))
        self.assertFalse(hidden_tool.is_directly_visible())


if __name__ == "__main__":
    unittest.main()
