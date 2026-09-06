from __future__ import annotations

import asyncio

import pytest

from examples.coding.bridge import CodeToolBridge
from examples.coding.protocol import CodingProtocolError
from examples.coding.schema import PythonToolSpec
from roboagent.message import ArtifactReferenceContent, FrozenJsonObject
from roboagent.tool import ToolExecutionResult, ToolJsonContent, ToolTextContent


class FakeExecution:
    def __init__(self, result: ToolExecutionResult) -> None:
        self.result = result
        self.calls = []

    async def execute_nested_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def _spec() -> PythonToolSpec:
    return PythonToolSpec(
        "read_file",
        "read_file",
        "Read.",
        (("path", {"type": "string"}),),
        frozenset({"path"}),
    )


def test_bridge_routes_to_nested_executor_and_caches_duplicate() -> None:
    async def check() -> None:
        execution = FakeExecution(
            ToolExecutionResult("call", "read_file", content=(ToolTextContent("ok"),))
        )
        bridge = CodeToolBridge(execution, (_spec(),))
        first = await bridge.execute("request", "read_file", {"path": "a"})
        second = await bridge.execute("request", "read_file", {"path": "a"})
        assert first == second == {"ok": True, "value": {"kind": "text", "value": "ok"}}
        assert len(execution.calls) == 1
        with pytest.raises(CodingProtocolError):
            await bridge.execute("request", "read_file", {"path": "b"})

    asyncio.run(check())


def test_bridge_rejects_recursion_and_maps_all_content() -> None:
    async def check() -> None:
        artifact = ArtifactReferenceContent(
            "workspace://files/a", "text/plain", 1, "sha256:" + "a" * 64, "a"
        )
        execution = FakeExecution(
            ToolExecutionResult(
                "call",
                "read_file",
                content=(
                    ToolTextContent("x"),
                    ToolJsonContent(FrozenJsonObject({"ok": True})),
                    artifact,
                ),
            )
        )
        bridge = CodeToolBridge(execution, (_spec(),))
        value = await bridge.execute("one", "read_file", {"path": "a"})
        assert value["value"]["kind"] == "tuple"
        assert [item["kind"] for item in value["value"]["value"]] == [
            "text",
            "json",
            "artifact",
        ]
        rejected = await bridge.execute("two", "execute_python", {"code": "1"})
        assert rejected["error"]["code"] == "tool_not_allowed"
        unknown = await bridge.execute("three", "other", {})
        assert unknown["error"]["code"] == "tool_not_allowed"

    asyncio.run(check())
