from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from pydantic import BaseModel

from roboagent.runtime import ModelContext, ToolCall
from roboagent.tool import ResolutionContext, Tool, ToolManager, ToolSpec


class Args(BaseModel): value: int


@dataclass
class Token:
    cancelled: bool = False


class NativeToolTests(unittest.TestCase):
    def _tool(self, name: str = "map.read") -> Tool:
        return Tool.from_spec(ToolSpec(name=name, description="Read", group="map", source="test"), Args,
                              lambda params, _: {"value": params.value})

    def test_validation_execution_and_schema(self) -> None:
        tool = self._tool()
        params = tool.validate({"value": 2})
        self.assertEqual(tool.definition.name, "map.read")
        self.assertEqual(asyncio.run(tool.execute(params, type("I", (), {"cancellation": Token()})())).content, '{"value": 2}')

    def test_manager_preserves_visibility_and_skill_authorization(self) -> None:
        manager = ToolManager(); manager.register(self._tool()); manager.register(self._tool("pose.read"))
        skill = type("Skill", (), {"allowed_tools": ("pose.read",)})()
        self.assertEqual([item.name for item in manager.get_tools(ResolutionContext("agent", activated_skills=(skill,)))], ["pose.read"])


if __name__ == "__main__": unittest.main()
