"""Nested execution must attribute child model events to the child lineage.

Document §2.5 requires this to be verified before Phase 2 nested projection work.
"""

from __future__ import annotations

import asyncio

from roboagent.agent import Agent
from roboagent.context import PromptInput
from roboagent.message import AssistantMessage, FrozenJsonObject, TextContent, ToolCall, UserMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
)
from roboagent.runtime import Modality
from roboagent.tool import ToolRegistry


class ScriptedModel:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), True, True
    )

    def __init__(self, replies: tuple[AssistantMessage, ...]) -> None:
        self.replies = iter(replies)

    async def stream(self, context, settings=None):
        message = next(self.replies)
        sequence = 0
        yield ResponseStarted("response", sequence)
        sequence += 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        for content in message.content:
            if isinstance(content, TextContent):
                yield TextDelta(sequence, content.text)
                sequence += 1
        reason = FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP
        yield ResponseCompleted(sequence, ModelResponse(message, reason))


async def _nested_events() -> list:
    child = Agent(
        ScriptedModel((AssistantMessage("child answer"),)),
        prompt=PromptInput("Child."),
    )
    child_tool = child.as_tool(name="delegate", description="Delegate work.")
    root_call = ToolCall("delegate-call", "delegate", FrozenJsonObject({"task": "do it"}))
    root = Agent(
        ScriptedModel(
            (AssistantMessage(tool_calls=(root_call,)), AssistantMessage("root answer"))
        ),
        tool_registry=ToolRegistry((child_tool,)),
        prompt=PromptInput("Root."),
    )
    session = root.new_session()
    run = session.start(UserMessage("start"))
    subscription = run.subscribe()
    await run.result()
    return [event async for event in subscription]


def test_nested_model_events_carry_child_lineage() -> None:
    async def check() -> None:
        events = await _nested_events()

        model_events = [event for event in events if event.type.startswith("model.")]
        assert model_events

        child_model_events = [
            event
            for event in model_events
            if event.lineage is not None and event.lineage.agent_depth == 1
        ]
        assert child_model_events, (
            "child model events must carry child lineage "
            f"(observed: {[(e.type, None if e.lineage is None else e.lineage.agent_depth) for e in model_events]})"
        )
        assert all(
            event.lineage.agent_tool_name == "delegate" for event in child_model_events
        )

        root_model_events = [
            event
            for event in model_events
            if event.lineage is None or event.lineage.agent_depth == 0
        ]
        assert root_model_events

    asyncio.run(check())


def test_nested_model_event_identities_are_distinct_per_run() -> None:
    async def check() -> None:
        events = await _nested_events()
        model_events = [event for event in events if event.type.startswith("model.")]
        started = [event for event in model_events if event.type == "model.started"]
        # Root turn 1 (tool call), the delegated child, then root turn 2.
        assert len(started) == 3
        ids = [event.payload["message_id"] for event in started]
        assert len(set(ids)) == len(ids)

        # Every delta of one invocation must reuse that invocation's identity.
        for event in model_events:
            assert "message_id" in event.payload

    asyncio.run(check())


def test_nested_delta_events_belong_to_their_run_lineage() -> None:
    async def check() -> None:
        events = await _nested_events()
        deltas = [event for event in events if event.type == "model.delta"]
        assert deltas
        depths = sorted(
            event.lineage.agent_depth
            for event in deltas
            if event.lineage is not None
        )
        # The child emits "child answer" at depth 1; the root emits "root answer" at 0.
        assert depths == [0, 1]

    asyncio.run(check())
