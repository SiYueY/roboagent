from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from roboagent.context import ModelContext
from roboagent.message import AssistantMessage, BytesSource, FrozenJsonObject, ImageContent, ToolCall, UserMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelCapabilityError,
    ModelProtocolError,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageUpdated,
    collect_model_stream,
)
from roboagent.runtime import Modality
from roboagent.tool import ToolDefinition
from roboagent.model.client import _stream_chunks


@dataclass
class EventModel:
    events: tuple[object, ...]
    capabilities: ModelCapabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), True, True
    )
    closed: bool = False
    called: bool = False

    async def stream(self, context, settings=None):
        self.called = True
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True


def test_collects_text_tool_usage_and_closes_stream() -> None:
    async def check() -> None:
        call = ToolCall("id", "lookup", FrozenJsonObject({"q": "x"}))
        response = ModelResponse(AssistantMessage("thinking", (call,)), FinishReason.TOOL_CALL, Usage(2, 3, 5))
        model = EventModel(
            (
                ResponseStarted("r", 0),
                TextDelta(1, "thinking"),
                ToolCallStarted(2, 0, "id", "lookup"),
                ToolCallArgumentsDelta(3, 0, "id", '{"q":"x"}'),
                ToolCallCompleted(4, 0, call),
                UsageUpdated(5, Usage(2, 3, 5)),
                ResponseCompleted(6, response),
            )
        )
        assert await collect_model_stream(model, ModelContext(None, (), ())) == response
        assert model.closed

    asyncio.run(check())


@pytest.mark.parametrize(
    ("events", "code"),
    [
        ((TextDelta(0, "x"),), "invalid_stream_sequence"),
        ((ResponseStarted("r", 0), ResponseStarted("r", 1)), "duplicate_response_started"),
        ((ResponseStarted("r", 0), ToolCallArgumentsDelta(1, 0, "x", "{}")), "invalid_tool_call_delta_state"),
        ((ResponseStarted("r", 0),), "missing_terminal_response"),
    ],
)
def test_stream_protocol_failures(events: tuple[object, ...], code: str) -> None:
    async def check() -> None:
        model = EventModel(events)
        with pytest.raises(ModelProtocolError) as caught:
            await collect_model_stream(model, ModelContext(None, (), ()))
        assert caught.value.code == code
        assert model.closed

    asyncio.run(check())


def test_duplicate_call_id_and_final_mismatch_rejected() -> None:
    async def check() -> None:
        first = ToolCall("same", "one", FrozenJsonObject())
        second = ToolCall("same", "two", FrozenJsonObject())
        duplicate = EventModel((ResponseStarted("r", 0), ToolCallStarted(1, 0, "same", "one"), ToolCallCompleted(2, 0, first), ToolCallStarted(3, 1, "same", "two")))
        with pytest.raises(ModelProtocolError, match="unique") as caught:
            await collect_model_stream(duplicate, ModelContext(None, (), ()))
        assert caught.value.code == "duplicate_tool_call_id"

        mismatch = EventModel((ResponseStarted("r", 0), ResponseCompleted(1, ModelResponse(AssistantMessage(tool_calls=(second,)), FinishReason.TOOL_CALL))))
        with pytest.raises(ModelProtocolError) as caught:
            await collect_model_stream(mismatch, ModelContext(None, (), ()))
        assert caught.value.code == "tool_call_response_mismatch"

    asyncio.run(check())


def test_capability_validation_precedes_model_invocation() -> None:
    async def check() -> None:
        definition = ToolDefinition("lookup", "Lookup.", FrozenJsonObject({"type": "object"}))
        model = EventModel((), ModelCapabilities(frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False))
        with pytest.raises(ModelCapabilityError) as caught:
            await collect_model_stream(model, ModelContext(None, (UserMessage("x"),), (definition,)))
        assert caught.value.code == "model_does_not_support_tools"
        assert not model.called
        assert not model.closed

    asyncio.run(check())


def test_parallel_tool_calls_require_declared_capability() -> None:
    async def check() -> None:
        first = ToolCall("first", "lookup", FrozenJsonObject())
        second = ToolCall("second", "lookup", FrozenJsonObject())
        response = ModelResponse(AssistantMessage(tool_calls=(first, second)), FinishReason.TOOL_CALL)
        model = EventModel(
            (
                ResponseStarted("r", 0),
                ToolCallStarted(1, 0, first.id, first.name),
                ToolCallCompleted(2, 0, first),
                ToolCallStarted(3, 1, second.id, second.name),
                ToolCallCompleted(4, 1, second),
                ResponseCompleted(5, response),
            ),
            ModelCapabilities(
                frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), True, False
            ),
        )
        with pytest.raises(ModelCapabilityError) as caught:
            await collect_model_stream(model, ModelContext(None, (UserMessage("x"),), ()))
        assert caught.value.code == "parallel_tool_calls_unsupported"
        assert model.called and model.closed

    asyncio.run(check())


def test_output_modality_requires_declared_capability() -> None:
    async def check() -> None:
        message = AssistantMessage((ImageContent(BytesSource(b"image"), "image/png"),))
        model = EventModel(
            (ResponseStarted("r", 0), ResponseCompleted(1, ModelResponse(message, FinishReason.STOP))),
            ModelCapabilities(
                frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False
            ),
        )
        with pytest.raises(ModelCapabilityError) as caught:
            await collect_model_stream(model, ModelContext(None, (UserMessage("x"),), ()))
        assert caught.value.code == "unsupported_output_modality"
        assert model.called and model.closed

    asyncio.run(check())


def test_openai_adapter_assembles_fragmented_json_and_generates_stable_id() -> None:
    async def check() -> None:
        def fragment(arguments: str, *, name: str | None = None):
            function = SimpleNamespace(name=name, arguments=arguments)
            return SimpleNamespace(index=0, id=None, function=function)

        def chunk(*calls, finish_reason=None):
            delta = SimpleNamespace(content=None, tool_calls=calls)
            choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
            return SimpleNamespace(id="provider-response", usage=None, choices=(choice,))

        class Stream:
            def __aiter__(self):
                async def iterate():
                    yield chunk(fragment('{"value":', name="lookup"))
                    yield chunk(fragment("1}"))
                    yield chunk(finish_reason="tool_calls")

                return iterate()

        events = [event async for event in _stream_chunks(Stream(), "model")]
        started = next(event for event in events if isinstance(event, ToolCallStarted))
        completed = next(event for event in events if isinstance(event, ToolCallCompleted))
        assert completed.call.id == started.call_id
        assert completed.call.arguments == {"value": 1}
        assert [event.sequence for event in events] == list(range(len(events)))
        assert sum(isinstance(event, ResponseStarted) for event in events) == 1
        assert sum(isinstance(event, ResponseCompleted) for event in events) == 1

    asyncio.run(check())
