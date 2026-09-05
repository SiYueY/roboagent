from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest

from roboagent.context import (
    ContextError,
    ContextRequest,
    ContextSnapshot,
    FullContextManager,
    MessageSegment,
    PromptInput,
    WindowContextManager,
)
from roboagent.message import (
    AssistantMessage,
    FrozenJsonArray,
    FrozenJsonObject,
    ProtocolError,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UserMessage,
    canonical_json_dumps,
    freeze_json,
)
from roboagent.runtime import RuntimeCancellation
from roboagent.model import ModelCapabilities, ModelSettings
from roboagent.skill import SkillMetadata, SkillSource
from roboagent.tool import ToolDefinition, ToolErrorInfo


def test_frozen_json_is_deep_detached_ordered_and_stable() -> None:
    source = {"z": [1, {"x": True}], "a": None}
    frozen = freeze_json(source)
    source["z"][1]["x"] = False  # type: ignore[index]
    assert isinstance(frozen, FrozenJsonObject)
    assert isinstance(frozen["z"], FrozenJsonArray)
    assert list(frozen) == ["z", "a"]
    assert frozen == {"z": [1, {"x": True}], "a": None}
    assert canonical_json_dumps(frozen) == '{"z":[1,{"x":true}],"a":null}'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, b"x", object()])
def test_frozen_json_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ProtocolError):
        freeze_json(value)


def test_frozen_json_rejects_non_string_keys() -> None:
    with pytest.raises(ProtocolError):
        FrozenJsonObject({1: "bad"})  # type: ignore[dict-item]


def test_context_prompt_order_and_atomic_window() -> None:
    async def check() -> None:
        cancellation = RuntimeCancellation()
        call = ToolCall("c", "lookup", FrozenJsonObject())
        error = ToolErrorInfo("failed", "no")
        transcript = (
            UserMessage("old"),
            AssistantMessage(tool_calls=(call,)),
            ToolResultMessage("c", "lookup", ToolResultStatus.ERROR, "no", error),
            UserMessage("new"),
        )
        definition = ToolDefinition("lookup", "Lookup data.", FrozenJsonObject({"type": "object"}))

        metadata = SkillMetadata("skill-a", "  useful\n guidance ", Path("/internal"), SkillSource.PROJECT)
        snapshot = ContextSnapshot("session", transcript, PromptInput("Base {name}", FrozenJsonObject({"name": "prompt"})), (definition,), (metadata,))
        request = ContextRequest(snapshot, ModelSettings(), ModelCapabilities(), None)
        prepared = await WindowContextManager(max_messages=2).prepare(request, cancellation)
        context = prepared.model_context
        assert context.segments == (MessageSegment(UserMessage("new", timestamp=transcript[-1].timestamp)),)
        assert context.system_prompt is not None
        assert context.system_prompt.index("Base prompt") < context.system_prompt.index("RoboAgent runtime")
        assert context.system_prompt.index("RoboAgent runtime") < context.system_prompt.index("Available skills")
        assert "useful guidance" in context.system_prompt

    asyncio.run(check())


def test_context_rejects_partial_exchange_and_cancellation() -> None:
    async def check() -> None:
        call = ToolCall("c", "lookup", FrozenJsonObject())
        with pytest.raises(ContextError):
            ContextSnapshot("session", (AssistantMessage(tool_calls=(call,)),), None, ())
        cancellation = RuntimeCancellation()
        cancellation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await FullContextManager().prepare(
                ContextRequest(ContextSnapshot("session", (), None, ()), ModelSettings(), ModelCapabilities(), None),
                cancellation,
            )

    asyncio.run(check())
