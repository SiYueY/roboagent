import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from roboagent.context import (
    CompactingContextManager,
    CompactionPolicy,
    ContextBudget,
    ContextBudgetError,
    ContextDataSegment,
    ContextError,
    ContextRequest,
    ContextSnapshot,
    FullContextManager,
    MessageSegment,
    ModelContext,
    SummaryResult,
    TokenEstimate,
    WindowContextManager,
)
from roboagent.message import (
    AssistantMessage,
    FrozenJsonObject,
    JsonContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UserMessage,
)
from roboagent.model import ModelCapabilities, ModelSettings
from roboagent.model.client import _messages, validate_model_context
from roboagent.runtime import RuntimeCancellation
from roboagent.tool import ToolDefinition
from roboagent.world import EntityRef, WorldEntry, WorldSnapshot, WorldStatus

NOW = datetime.now(timezone.utc)


def entry(entity, status=WorldStatus.KNOWN, value="red"):
    return WorldEntry(
        EntityRef(entity),
        "color",
        status,
        value if status in (WorldStatus.KNOWN, WorldStatus.STALE) else None,
        (),
        (),
        (),
        None,
    )


def request(entries, messages=None, tools=()):
    snapshot = WorldSnapshot("world", 3, NOW, tuple(entries))
    return ContextRequest(
        ContextSnapshot(
            "session",
            tuple(messages or (UserMessage("red cube on table"),)),
            None,
            tools,
        ),
        ModelSettings(),
        ModelCapabilities(),
        None,
        snapshot,
    )


def test_deterministic_relevance_explicit_entities_tools_and_no_nl_grounding():
    async def check():
        entries = [
            entry("natural_target"),
            entry("other"),
            entry("tool"),
            entry("explicit", WorldStatus.CONFLICTED),
            entry("self"),
        ]
        tool = ToolDefinition(
            "work",
            "Work.",
            FrozenJsonObject({"type": "object", "x-world-entities": ["tool"]}),
        )
        req = request(
            entries,
            (UserMessage((JsonContent({"entity_ref": {"id": "explicit"}}),)),),
            (tool,),
        )
        manager = FullContextManager()
        first = (await manager.prepare(req, RuntimeCancellation())).model_context
        second = (await manager.prepare(req, RuntimeCancellation())).model_context
        assert first == second
        projected = json.loads(first.segments[0].text)
        assert [item["subject"]["id"] for item in projected[:3]] == [
            "self",
            "explicit",
            "tool",
        ]
        natural = (
            await manager.prepare(request(entries), RuntimeCancellation())
        ).model_context
        ids = [item["subject"]["id"] for item in json.loads(natural.segments[0].text)]
        assert ids[-1] == "natural_target"  # no secret grounding of natural text
        assert projected[1]["status"] == "conflicted" and projected[1]["value"] is None

    asyncio.run(check())


def test_data_before_transcript_provider_encoding_and_closed_union():
    async def check():
        call = ToolCall("c", "work")
        messages = (
            UserMessage("go"),
            AssistantMessage(tool_calls=(call,)),
            ToolResultMessage("c", "work", ToolResultStatus.SUCCESS, "done"),
        )
        prepared = await FullContextManager().prepare(
            request([entry("self")], messages), RuntimeCancellation()
        )
        context = prepared.model_context
        validate_model_context(ModelCapabilities(tool_calling=True), context)
        encoded, resources = await _messages(context, None)
        assert not resources
        assert [item["role"] for item in encoded] == [
            "system",
            "user",
            "user",
            "assistant",
            "tool",
        ]
        assert "Untrusted" in encoded[1]["content"]
        with pytest.raises(ContextError):
            ModelContext(None, (MessageSegment(messages[0]), context.segments[0]), ())
        with pytest.raises(ContextError):
            ModelContext(
                None,
                (
                    MessageSegment(messages[1]),
                    context.segments[0],
                    MessageSegment(messages[2]),
                ),
                (),
            )

    asyncio.run(check())


def test_bounded_projection_truncation_and_window_consumer():
    async def check():
        entries = [entry(f"e{index}") for index in range(100)]
        for manager in (FullContextManager(), WindowContextManager(max_messages=1)):
            result = await manager.prepare(request(entries), RuntimeCancellation())
            segment = result.model_context.segments[0]
            assert segment.truncated and segment.omitted_count == 36
            assert len(json.loads(segment.text)) == 64

    asyncio.run(check())


class SizeEstimator:
    def estimate(self, context):
        size = len(context.system_prompt or "")
        for segment in context.segments:
            size += (
                len(segment.text)
                if hasattr(segment, "text")
                else len(str(segment.message.content))
            )
        return TokenEstimate(size)


class Summarizer:
    def __init__(self):
        self.calls = []

    async def summarize(self, *, existing_summary, messages, cancellation):
        self.calls.append(messages)
        return SummaryResult("short")


def test_budget_drops_world_first_and_summarizer_sees_only_conversation():
    async def check():
        summarizer = Summarizer()
        manager = CompactingContextManager(
            budget=ContextBudget(1500),
            estimator=SizeEstimator(),
            summarizer=summarizer,
            policy=CompactionPolicy(target_ratio=1),
            provider_default_reserve=0,
        )
        messages = (
            UserMessage("x" * 800),
            AssistantMessage("y" * 800),
            UserMessage("latest"),
        )
        req = request(
            [entry("low", value="z" * 500), entry("self", WorldStatus.STALE)], messages
        )
        prepared = await manager.prepare(req, RuntimeCancellation())
        segment = prepared.model_context.segments[0]
        assert segment.truncated and segment.omitted_count == 1
        assert json.loads(segment.text)[0]["status"] == "stale"
        assert summarizer.calls == [messages[:2]]
        assert SizeEstimator().estimate(prepared.model_context).input_tokens <= 1500

    asyncio.run(check())


def test_relevant_uncertainty_over_budget_is_rejected_not_erased():
    async def check():
        manager = CompactingContextManager(
            budget=ContextBudget(700),
            estimator=SizeEstimator(),
            provider_default_reserve=0,
        )
        with pytest.raises(ContextBudgetError):
            await manager.prepare(
                request([entry("self", WorldStatus.STALE, "x" * 4000)]),
                RuntimeCancellation(),
            )
        req = replace(
            request([entry("self", WorldStatus.STALE, "x" * 4000)]),
            model_capabilities=ModelCapabilities(context_window=700),
            model_settings=ModelSettings(max_output_tokens=1),
        )
        with pytest.raises(ContextBudgetError):
            await FullContextManager().prepare(req, RuntimeCancellation())

    asyncio.run(check())


def test_full_context_custom_estimator_covers_data_and_media():
    async def check():
        from roboagent.message import BytesSource, ImageContent

        class MediaEstimator:
            def __init__(self):
                self.inputs = []

            def estimate(self, context):
                self.inputs.append(context)
                return TokenEstimate(100)

        estimator = MediaEstimator()
        manager = FullContextManager(budget=ContextBudget(1200), estimator=estimator)
        req = request(
            [entry("self")],
            (UserMessage((ImageContent(BytesSource(b"image"), "image/jpeg"),)),),
        )
        context = (await manager.prepare(req, RuntimeCancellation())).model_context
        assert estimator.inputs == [context]
        assert isinstance(context.segments[0], ContextDataSegment)

    asyncio.run(check())


def test_coding_adapter_preserves_context_data_as_data_segment():
    from examples.coding.model_adapter import _project_context

    segment = ContextDataSegment("world", "id", 1, NOW, "[]")
    original = ModelContext("policy", (segment, MessageSegment(UserMessage("go"))), ())
    projected = _project_context(original)
    assert projected.segments[0] is segment


def test_world_search_result_entities_are_relevant_and_uncertainty_is_protected():
    async def check():
        from roboagent.world import EntityCandidate, EntitySearchResult
        from roboagent.world._codec import world_data
        from roboagent.message import freeze_json

        candidates = EntitySearchResult(
            "world",
            3,
            (
                EntityCandidate(
                    EntityRef("target"), FrozenJsonObject({"color": "red"}), ()
                ),
            ),
            False,
        )
        messages = (
            UserMessage("find red"),
            AssistantMessage(
                tool_calls=(
                    ToolCall(
                        "find", "world_find_entities", {"filters": {"color": "red"}}
                    ),
                )
            ),
            ToolResultMessage(
                "find",
                "world_find_entities",
                ToolResultStatus.SUCCESS,
                (JsonContent(freeze_json(world_data(candidates))),),
            ),
        )
        entries = [entry("target", WorldStatus.STALE)] + [
            entry(f"other{index}") for index in range(80)
        ]
        result = await FullContextManager().prepare(
            request(entries, messages), RuntimeCancellation()
        )
        segment = result.model_context.segments[0]
        projected = json.loads(segment.text)
        assert projected[0]["subject"]["id"] == "target"
        assert projected[0]["status"] == "stale"
        assert segment.omitted_count == 17

        wrong_world = replace(candidates, world_id="other-world")
        messages = (UserMessage((JsonContent(freeze_json(world_data(wrong_world))),)),)
        result = await FullContextManager().prepare(
            request(entries, messages), RuntimeCancellation()
        )
        assert "target" not in {
            item["subject"]["id"]
            for item in json.loads(result.model_context.segments[0].text)
        }

    asyncio.run(check())


def test_world_query_entity_argument_is_an_explicit_reference():
    async def check():
        from roboagent.world import World, create_world_tools

        tools = tuple(tool.definition for tool in create_world_tools(World("world")))
        call = ToolCall(
            "query", "world_query", {"entity_id": "target", "predicate": "color"}
        )
        messages = (
            UserMessage("query"),
            AssistantMessage(tool_calls=(call,)),
            ToolResultMessage(
                "query", "world_query", ToolResultStatus.SUCCESS, "unknown"
            ),
        )
        result = await FullContextManager().prepare(
            request(
                [entry("target", WorldStatus.UNKNOWN), entry("newer")], messages, tools
            ),
            RuntimeCancellation(),
        )
        assert (
            json.loads(result.model_context.segments[0].text)[0]["subject"]["id"]
            == "target"
        )

    asyncio.run(check())


def test_model_input_projection_is_validated_and_minted_by_agent_configuration():
    from roboagent import Agent
    from tests.integration.test_embodied_loop import ScriptedModel

    with pytest.raises(TypeError, match="model_input_projection"):
        Agent(ScriptedModel(), model_input_projection=3)
    with pytest.raises(TypeError, match="model_input_projection"):
        replace(request([]), model_input_projection=3)

    async def check():
        from roboagent.context import TokenEstimationError

        manager = FullContextManager()
        with pytest.raises(
            TokenEstimationError, match="invalid_model_input_projection"
        ):
            await manager.prepare(
                replace(request([]), model_input_projection=lambda context: None),
                RuntimeCancellation(),
            )

    asyncio.run(check())


def test_relation_refs_are_relevant_but_json_lookalikes_are_not():
    from roboagent.context.world import _entity_refs
    from roboagent.world import WorldCandidate
    from roboagent.world._codec import world_data

    relation = WorldCandidate(EntityRef("target"), (), (), None)
    literal = WorldCandidate({"entity_ref": {"id": "target"}}, (), (), None)
    assert _entity_refs(world_data(relation), "world") == {"target"}
    assert _entity_refs(world_data(literal), "world") == set()
