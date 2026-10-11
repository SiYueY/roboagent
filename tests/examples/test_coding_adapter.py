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


def test_context_manager_owns_coding_projection_and_retry_budget() -> None:
    async def check() -> None:
        from roboagent.context import (
            CompactingContextManager,
            ContextBudget,
            ContextBudgetError,
            ContextRequest,
            ContextSnapshot,
        )
        from roboagent.model import ModelSettings
        from roboagent.runtime import RuntimeCancellation

        provider = ScriptedProvider([("unused", FinishReason.STOP)])
        provider.capabilities = ModelCapabilities(context_window=100)
        adapter = CodingModelAdapter(provider)
        request = ContextRequest(
            ContextSnapshot("session", (UserMessage("go"),), None, ()),
            ModelSettings(max_output_tokens=1),
            adapter.capabilities,
            None,
            model_input_projection=adapter.project_model_input,
        )
        manager = CompactingContextManager(
            budget=ContextBudget(100), provider_default_reserve=0
        )
        with pytest.raises(ContextBudgetError) as caught:
            await manager.prepare(request, RuntimeCancellation())
        assert caught.value.code == "context_budget_exceeded"
        assert provider.calls == 0

    asyncio.run(check())


def test_context_budget_covers_reset_notice_and_every_protocol_retry() -> None:
    async def check():
        from dataclasses import replace
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from roboagent.context import (
            ContextBudget,
            ContextDataSegment,
            ContextRequest,
            ContextSnapshot,
            FullContextManager,
            ConservativeTokenEstimator,
        )
        from roboagent.model import ModelSettings
        from roboagent.runtime import RuntimeCancellation
        from roboagent.world import WorldSnapshot

        provider = ScriptedProvider(
            [("```python\n", FinishReason.STOP), ("done", FinishReason.STOP)]
        )
        provider.capabilities = ModelCapabilities(context_window=2048)
        adapter = CodingModelAdapter(provider)
        adapter.worker_client = SimpleNamespace(pending_reset_notice=True)
        state = CodingRunState("run", 2)
        adapter.bind(state)
        try:
            request = ContextRequest(
                ContextSnapshot("session", (UserMessage("inspect"),), None, ()),
                ModelSettings(max_output_tokens=128),
                adapter.capabilities,
                None,
                WorldSnapshot("w", 1, datetime.now(timezone.utc), ()),
                adapter.project_model_input,
            )
            prepared = await FullContextManager(budget=ContextBudget(2048)).prepare(
                request, RuntimeCancellation()
            )
            canonical = prepared.model_context
            assert isinstance(canonical.segments[0], ContextDataSegment)
            assert len(canonical.segments) == 2  # envelopes never enter transcript
            upper_bound = (
                ConservativeTokenEstimator()
                .estimate(adapter.project_model_input(canonical))
                .input_tokens
            )
            response = await collect_model_stream(
                adapter, canonical, request.model_settings
            )
            assert (
                response.message.content[0].text == "done"
                and len(provider.contexts) == 2
            )
            for actual in provider.contexts:
                estimate = ConservativeTokenEstimator().estimate(actual).input_tokens
                assert estimate <= upper_bound <= 2048 - 128
                assert actual.segments[0] is canonical.segments[0]
            assert adapter.worker_client.pending_reset_notice is False
            # Reserving exactly the bound succeeds; one token less is rejected
            # by ContextManager before issuing any provider request.
            from roboagent.context import ContextBudgetError

            estimate = ConservativeTokenEstimator().estimate(canonical).input_tokens
            limit = max(estimate, upper_bound) + 1
            await FullContextManager(budget=ContextBudget(limit)).prepare(
                replace(request, model_settings=ModelSettings(max_output_tokens=1)),
                RuntimeCancellation(),
            )
            with pytest.raises(ContextBudgetError):
                await FullContextManager(budget=ContextBudget(limit - 1)).prepare(
                    replace(request, model_settings=ModelSettings(max_output_tokens=1)),
                    RuntimeCancellation(),
                )
        finally:
            adapter.unbind(state)

    asyncio.run(check())
