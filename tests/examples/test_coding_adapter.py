from __future__ import annotations

import asyncio

import pytest

from examples.coding.model_adapter import (
    CodingModelAdapter,
    CodingRunState,
    _projected_python_fence,
)
from examples.coding.protocol import EXECUTE_PROTOCOL
from roboagent.context import (
    MessageSegment,
    ModelContext,
    SummarySegment,
    WorkspaceReferenceSegment,
)
from roboagent.message import (
    AssistantMessage,
    JsonContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UserMessage,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelProtocolError,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    Usage,
    collect_model_stream,
)
from roboagent.runtime import Modality


class ScriptedProvider:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT})
    )

    def __init__(self, replies: list[tuple[str, FinishReason]]) -> None:
        self.replies = iter(replies)
        self.contexts = []
        self.calls = 0

    async def stream(self, context, settings=None):
        self.contexts.append(context)
        self.calls += 1
        text, reason = next(self.replies)
        message = AssistantMessage(text)
        yield ResponseStarted(f"r{self.calls}", 0)
        yield TextDelta(1, text)
        yield ResponseCompleted(2, ModelResponse(message, reason, Usage(1, 2, 3)))


def test_projected_python_fence_cannot_be_closed_by_code_content() -> None:
    projected = _projected_python_fence("value = '```'")
    assert projected.startswith("````python\n")
    assert projected.endswith("\n````")


def test_adapter_buffers_retry_and_emits_one_python_tool_call() -> None:
    async def check() -> None:
        provider = ScriptedProvider(
            [
                ("```python\n", FinishReason.STOP),
                ("reason\n```python\nprint(1)\n```", FinishReason.STOP),
            ]
        )
        adapter = CodingModelAdapter(provider)
        state = CodingRunState("run", 2)
        adapter.bind(state)
        try:
            response = await collect_model_stream(
                adapter,
                ModelContext(None, (MessageSegment(UserMessage("go")),), ()),
            )
        finally:
            adapter.unbind(state)
        assert response.finish_reason is FinishReason.TOOL_CALL
        assert response.message.tool_calls[0].name == "execute_python"
        assert response.message.tool_calls[0].arguments["code"] == "print(1)\n"
        assert response.message.content[0].text == "reason\n"
        assert response.usage == Usage(2, 4, 6)
        assert state.provider_calls_used == 2
        assert all(not context.tools for context in provider.contexts)
        assert provider.contexts[1].segments[-1].message.role == "user"

    asyncio.run(check())


def test_provider_budget_and_native_finish_safety() -> None:
    async def check() -> None:
        provider = ScriptedProvider([("```python\n1\n```", FinishReason.LENGTH)])
        adapter = CodingModelAdapter(provider, max_protocol_retries=0)
        state = CodingRunState("run", 1)
        adapter.bind(state)
        try:
            with pytest.raises(ModelProtocolError) as caught:
                await collect_model_stream(adapter, ModelContext(None, (), ()))
            assert caught.value.code == "coding_provider_incomplete_response"
            with pytest.raises(ModelProtocolError) as exhausted:
                await collect_model_stream(adapter, ModelContext(None, (), ()))
            assert exhausted.value.code == "coding_provider_budget_exceeded"
        finally:
            adapter.unbind(state)

    asyncio.run(check())


def test_local_final_skips_provider_and_requires_complete_tail() -> None:
    async def check() -> None:
        provider = ScriptedProvider([])
        adapter = CodingModelAdapter(provider)
        state = CodingRunState("run", 1)
        adapter.bind(state)
        envelope = {
            "protocol": EXECUTE_PROTOCOL,
            "execution_status": "ok",
            "is_final": True,
            "final": {"kind": "text", "value": "done"},
        }
        action = AssistantMessage(
            tool_calls=(
                ToolCall("call", "execute_python", {"code": "final_answer('done')"}),
            )
        )
        result = ToolResultMessage(
            "call", "execute_python", ToolResultStatus.SUCCESS, (JsonContent(envelope),)
        )
        segments = (MessageSegment(action), MessageSegment(result))
        try:
            response = await collect_model_stream(
                adapter, ModelContext(None, segments, ())
            )
            assert response.message.content[0].text == "done"
            assert response.usage == Usage(0, 0, 0)
            assert provider.calls == 0
            with pytest.raises(ModelProtocolError) as caught:
                await collect_model_stream(
                    adapter,
                    ModelContext(None, segments, (), recent_tail_complete=False),
                )
            assert caught.value.code == "coding_context_tail_unavailable"
        finally:
            adapter.unbind(state)

    asyncio.run(check())


def test_all_supported_segments_project_and_unknown_cannot_sneak_through() -> None:
    async def check() -> None:
        provider = ScriptedProvider([("done", FinishReason.STOP)])
        adapter = CodingModelAdapter(provider)
        state = CodingRunState("run", 1)
        adapter.bind(state)
        try:
            await collect_model_stream(
                adapter,
                ModelContext(
                    None,
                    (
                        SummarySegment("old facts"),
                        WorkspaceReferenceSegment(
                            "workspace://files/a", "short", "text/plain"
                        ),
                        MessageSegment(UserMessage("go")),
                    ),
                    (),
                ),
            )
        finally:
            adapter.unbind(state)
        projected = provider.contexts[0]
        assert isinstance(projected.segments[0], SummarySegment)
        assert "Workspace reference" in projected.segments[1].message.content[0].text

    asyncio.run(check())


def test_projection_reserves_256_tokens() -> None:
    async def check() -> None:
        provider = ScriptedProvider([("unused", FinishReason.STOP)])
        provider.capabilities = ModelCapabilities(
            frozenset({Modality.TEXT}),
            frozenset({Modality.TEXT}),
            context_window=256,
        )
        adapter = CodingModelAdapter(provider)
        state = CodingRunState("run", 1)
        adapter.bind(state)
        try:
            with pytest.raises(ModelProtocolError) as caught:
                await collect_model_stream(adapter, ModelContext(None, (), ()))
            assert caught.value.code == "coding_projection_budget_exceeded"
            assert provider.calls == 0
        finally:
            adapter.unbind(state)

    asyncio.run(check())
