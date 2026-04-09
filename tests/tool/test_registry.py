from __future__ import annotations

import unittest

from roboagent.tool import Tool, ToolRegistry, ToolSpec
from roboagent.tool.errors import DuplicateToolError, ToolNotFoundError, ToolRegistrationError

from tests.tool._helpers import create_structured_tool


def build_tool(
    name: str,
    *,
    group: str = "default",
    source: str = "builtin",
    visible_by_default: bool = True,
    deferred: bool = False,
) -> Tool:
    """Build a runtime tool for registry tests."""
    return Tool.from_spec(
        create_structured_tool(name),
        ToolSpec(
            name=name,
            description=f"Tool {name}.",
            group=group,
            source=source,
            visible_by_default=visible_by_default,
            deferred=deferred,
        ),
    )


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_lookup_tool(self) -> None:
        registry = ToolRegistry()
        tool = build_tool("map.read", group="map")

        registry.register(tool)

        self.assertTrue(registry.has("map.read"))
        self.assertEqual(registry.get("map.read"), tool)
        self.assertEqual(registry.require("map.read"), tool)

    def test_register_without_replace_raises_duplicate(self) -> None:
        registry = ToolRegistry()
        tool = build_tool("map.read")
        registry.register(tool)

        with self.assertRaises(DuplicateToolError):
            registry.register(tool, replace=False)

    def test_batch_register_is_atomic_when_replace_is_false(self) -> None:
        existing = build_tool("map.read")
        new_tool = build_tool("pose.read")
        registry = ToolRegistry([existing])

        with self.assertRaises(DuplicateToolError):
            registry.register([new_tool, existing], replace=False)

        self.assertTrue(registry.has("map.read"))
        self.assertFalse(registry.has("pose.read"))

    def test_require_missing_tool_raises(self) -> None:
        registry = ToolRegistry()

        with self.assertRaises(ToolNotFoundError):
            registry.require("missing")

    def test_list_all_filters_and_sorts(self) -> None:
        registry = ToolRegistry(
            [
                build_tool("pose.read", group="pose", source="project"),
                build_tool("map.read", group="map", source="builtin"),
                build_tool("map.write", group="map", source="builtin"),
            ]
        )

        self.assertEqual(
            [tool.name for tool in registry.list_all(source="builtin", group="map")],
            ["map.read", "map.write"],
        )

    def test_register_rejects_runtime_tool_with_mismatched_base_tool_name(self) -> None:
        invalid_tool = Tool(
            base_tool=create_structured_tool("actual.name"),
            name="declared.name",
            description="Broken tool.",
            group="broken",
            source="builtin",
        )
        registry = ToolRegistry()

        with self.assertRaises(ToolRegistrationError):
            registry.register(invalid_tool)


if __name__ == "__main__":
    unittest.main()
