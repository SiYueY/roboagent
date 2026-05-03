from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from roboagent.agent import AgentBuilder
from roboagent.skill import Skill
from roboagent.tool import ToolManager, ToolSpec
from tests.tool._helpers import create_structured_tool


class AgentBuilderTests(unittest.TestCase):
    def test_builder_does_not_inject_skill_context_into_prompt(self) -> None:
        active = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="test",
            source_dir=Path("/tmp/nav-plan"),
            allowed_tools=("map.read",),
        )
        deprecated = Skill(
            name="old-nav",
            description="Old navigation skill.",
            source="test",
            source_dir=Path("/tmp/old-nav"),
            status="deprecated",
        )

        with patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create:
            result = AgentBuilder(
                model=object(),
                system_prompt="Base prompt.",
                skills=[active, deprecated],
            ).build()

        self.assertEqual(result, "graph")
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["system_prompt"], "Base prompt.")
        self.assertNotIn("skills", kwargs)
        self.assertEqual(kwargs["middleware"], ())

    def test_builder_uses_skill_allowed_tools_for_tool_manager_resolution(self) -> None:
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
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="test",
            source_dir=Path("/tmp/nav-plan"),
            allowed_tools=("pose.read",),
        )

        with patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create:
            AgentBuilder(model=object(), tool_manager=manager, skills=[skill]).build()

        _, kwargs = mock_create.call_args
        self.assertEqual([tool.name for tool in kwargs["tools"]], ["pose.read"])

    def test_builder_passes_middlewares_to_create_agent(self) -> None:
        middleware = object()

        with patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create:
            AgentBuilder(model=object(), middlewares=[middleware]).build()

        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["middleware"], [middleware])


if __name__ == "__main__":
    unittest.main()
