"""Runtime Event message identity and Tool effect projection invariants."""

from __future__ import annotations

import asyncio

from roboagent.agent import Agent
from roboagent.context import PromptInput
from roboagent.message import (
    AssistantMessage,
    FrozenJsonObject,
    TextContent,
    ToolCall,
    UserMessage,
)
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
from roboagent.runtime import Modality, RunStatus
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionMode,
    ToolRegistry,
    ToolTextContent,
)


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


async def _run_with_tool():
    call = ToolCall("call-1", "lookup", FrozenJsonObject({"value": 1}))
    model = ScriptedModel(
        (AssistantMessage(tool_calls=(call,)), AssistantMessage("done"))
    )

    async def handler(arguments, context):
        return ToolTextContent("ok")

    registry = ToolRegistry(
        (
            Tool(
                ToolDefinition(
                    "lookup",
                    "Look up a value.",
                    FrozenJsonObject(
                        {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        }
                    ),
                ),
                handler,
                ToolExecutionMode.CONCURRENT,
                ToolEffectKind.READ_ONLY,
            ),
        )
    )
    session = Agent(
        model, tool_registry=registry, prompt=PromptInput("Help.")
    ).new_session()
    run = session.start(UserMessage("go"))
    result = await run.result()
    events = [event async for event in run.subscribe()]
    return session, result, events


def test_model_events_share_committed_assistant_message_id() -> None:
    async def check() -> None:
        session, result, events = await _run_with_tool()
        assert result.status is RunStatus.COMPLETED

        model_events = [event for event in events if event.type.startswith("model.")]
        assert model_events
        assert all("message_id" in event.payload for event in model_events)

        by_turn: dict[int, set[str]] = {}
        for event in model_events:
            by_turn.setdefault(event.payload["turn"], set()).add(
                event.payload["message_id"]
            )
        # Every model event of one invocation shares exactly one identity.
        assert all(len(ids) == 1 for ids in by_turn.values())

        committed = [m for m in session.messages if isinstance(m, AssistantMessage)]
        assert committed
        assert {next(iter(ids)) for ids in by_turn.values()} == {
            message.message_id for message in committed
        }


def test_tool_events_carry_message_and_call_identity() -> None:
    async def check() -> None:
        _, _, events = await _run_with_tool()

        tool_events = [event for event in events if event.type.startswith("tool.")]
        assert tool_events
        for event in tool_events:
            assert event.payload["tool_call_id"] == "call-1"
            assert event.payload["tool_name"] == "lookup"
            assert isinstance(event.payload["message_id"], str)
            assert event.payload["message_id"]

        committed = [event for event in events if event.type == "tool_batch.committed"]
        assert len(committed) == 1
        assert committed[0].payload["tool_call_ids"] == ["call-1"]


def test_tool_batch_committed_exposes_per_tool_effect_details() -> None:
    async def check() -> None:
        _, result, events = await _run_with_tool()
        committed = next(
            event for event in events if event.type == "tool_batch.committed"
        )
        effects = committed.payload["effects"]
        assert len(effects) == 1
        effect = effects[0]
        assert effect["tool_call_id"] == "call-1"
        assert effect["tool_name"] == "lookup"
        assert effect["effect_status"] == "succeeded"
        assert effect["certainty"] in {"certain", "certain_no_effect", "unknown"}
        assert isinstance(committed.payload["message_id"], str)

        # The event projection must agree with the authoritative final effect.
        final = [item for item in result.effects if item.call_id == "call-1"]
        assert len(final) == 1
        assert effect["effect_status"] == final[0].status.value
        assert effect["certainty"] == (
            None if final[0].certainty is None else final[0].certainty.value
        )

    asyncio.run(check())


def test_model_event_identity_matches_persisted_message_identity(tmp_path) -> None:
    async def check() -> None:
        from roboagent.agent import LocalSessionRepository, Session

        root = tmp_path / "sessions"
        repository = LocalSessionRepository(root)
        call = ToolCall("call-1", "lookup", FrozenJsonObject({"value": 1}))
        model = ScriptedModel(
            (AssistantMessage(tool_calls=(call,)), AssistantMessage("done"))
        )

        async def handler(arguments, context):
            return ToolTextContent("ok")

        registry = ToolRegistry(
            (
                Tool(
                    ToolDefinition(
                        "lookup",
                        "Look up a value.",
                        FrozenJsonObject(
                            {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            }
                        ),
                    ),
                    handler,
                    ToolExecutionMode.CONCURRENT,
                    ToolEffectKind.READ_ONLY,
                ),
            )
        )
        agent = Agent(model, tool_registry=registry, prompt=PromptInput("Help."))
        session = agent.new_session(repository=repository)
        run = session.start(UserMessage("go"))
        await run.result()
        live_ids = [message.message_id for message in session.messages]

        reopened = await Session.open(
            agent=agent, session_id=session.session_id, repository=repository
        )
        assert [message.message_id for message in reopened.messages] == live_ids

    asyncio.run(check())
