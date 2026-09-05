from __future__ import annotations

import asyncio

import pytest

from roboagent.context import (
    CompactingContextManager,
    CompactionPolicy,
    ConservativeTokenEstimator,
    ContextBudget,
    ContextBudgetError,
    ContextRequest,
    ContextSnapshot,
    ContextSummary,
    MessageSegment,
    ModelContext,
    SummaryResult,
    SummarySegment,
    TokenEstimate,
    TokenEstimationError,
)
from roboagent.message import AssistantMessage, BytesSource, ImageContent, UserMessage, canonical_message_digest
from roboagent.model import ModelCapabilities, ModelSettings, Usage
from roboagent.runtime import RuntimeCancellation


class _LengthEstimator:
    def estimate(self, context: ModelContext) -> TokenEstimate:
        total = 10 + len(context.system_prompt or "")
        for segment in context.segments:
            if isinstance(segment, SummarySegment):
                total += len(segment.text)
            elif isinstance(segment, MessageSegment):
                total += sum(len(getattr(item, "text", "")) for item in segment.message.content)
        return TokenEstimate(total, True)


class _RecordingSummarizer:
    def __init__(self, text: str = "sum") -> None:
        self.text = text
        self.calls = []

    async def summarize(self, *, existing_summary, messages, cancellation):
        self.calls.append((existing_summary, messages))
        cancellation.raise_if_cancelled()
        return SummaryResult(self.text, Usage(2, 1, 3), "test")


def _request(messages, *, current=None, configured=200, capability=300, output=None):
    return ContextRequest(
        ContextSnapshot("session", tuple(messages), None, ()),
        ModelSettings(max_output_tokens=output),
        ModelCapabilities(context_window=capability),
        current,
    )


def test_compaction_uses_whole_request_budget_and_keeps_latest_user_turn() -> None:
    async def check() -> None:
        messages = (UserMessage("a" * 40), AssistantMessage("b" * 40), UserMessage("latest"))
        summarizer = _RecordingSummarizer()
        manager = CompactingContextManager(
            budget=ContextBudget(210),
            estimator=_LengthEstimator(),
            summarizer=summarizer,
            policy=CompactionPolicy(target_ratio=1),
            provider_default_reserve=0,
        )
        prepared = await manager.prepare(_request(messages, configured=210), RuntimeCancellation())
        summary = prepared.compaction_update.summary if prepared.compaction_update else None
        assert summary is not None and summary.source_end_exclusive == 2
        assert summary.source_digest == canonical_message_digest(messages[:2])
        assert summarizer.calls == [(None, messages[:2])]
        assert prepared.model_context.segments[-1] == MessageSegment(messages[-1])
        assert prepared.usage_delta == Usage(2, 1, 3)

    asyncio.run(check())


def test_incremental_compaction_passes_only_old_summary_and_new_groups() -> None:
    async def check() -> None:
        messages = (
            UserMessage("old"),
            AssistantMessage("old"),
            UserMessage("m" * 40),
            AssistantMessage("n" * 40),
            UserMessage("latest"),
        )
        old = ContextSummary(0, 2, canonical_message_digest(messages[:2]), "old sum", 1)
        summarizer = _RecordingSummarizer()
        manager = CompactingContextManager(
            budget=ContextBudget(210),
            estimator=_LengthEstimator(),
            summarizer=summarizer,
            policy=CompactionPolicy(target_ratio=1),
            provider_default_reserve=0,
        )
        prepared = await manager.prepare(_request(messages, current=old, configured=210), RuntimeCancellation())
        summary = prepared.compaction_update.summary if prepared.compaction_update else None
        assert summary is not None and summary.source_end_exclusive == 3
        assert summarizer.calls == [(old, messages[2:3])]
        assert summary.source_digest == canonical_message_digest(messages[:3])

    asyncio.run(check())


def test_invalid_summary_is_explicitly_cleared_even_below_threshold() -> None:
    async def check() -> None:
        messages = (UserMessage("small"),)
        invalid = ContextSummary(0, 1, "wrong", "stale", 1)
        manager = CompactingContextManager(
            budget=ContextBudget(250), estimator=_LengthEstimator(), provider_default_reserve=0
        )
        prepared = await manager.prepare(_request(messages, current=invalid), RuntimeCancellation())
        assert prepared.compaction_update is not None
        assert prepared.compaction_update.summary is None
        assert prepared.compaction_update.expected_summary_digest == "wrong"
        assert prepared.model_context.segments == (MessageSegment(messages[0]),)

    asyncio.run(check())


def test_effective_window_uses_minimum_and_output_reserve() -> None:
    async def check() -> None:
        manager = CompactingContextManager(
            budget=ContextBudget(200), estimator=_LengthEstimator(), provider_default_reserve=0
        )
        with pytest.raises(ContextBudgetError) as caught:
            await manager.prepare(
                _request((UserMessage("x"),), configured=200, capability=50, output=50),
                RuntimeCancellation(),
            )
        assert caught.value.code == "context_budget_invalid"

    asyncio.run(check())


def test_static_overhead_and_unestimable_media_fail_explicitly() -> None:
    async def check() -> None:
        manager = CompactingContextManager(budget=ContextBudget(20), provider_default_reserve=0)
        request = ContextRequest(
            ContextSnapshot("session", (UserMessage("x"),), None, ()),
            ModelSettings(),
            ModelCapabilities(context_window=20),
            None,
        )
        with pytest.raises(ContextBudgetError) as static:
            await manager.prepare(request, RuntimeCancellation())
        assert static.value.reason == "static_overhead"

        media = UserMessage((ImageContent(BytesSource(b"image"), "image/png"),))
        manager = CompactingContextManager(budget=ContextBudget(1000), provider_default_reserve=0)
        with pytest.raises(TokenEstimationError):
            await manager.prepare(_request((media,), configured=1000, capability=1000), RuntimeCancellation())

    asyncio.run(check())


def test_compaction_cancellation_returns_no_partial_update() -> None:
    async def check() -> None:
        cancellation = RuntimeCancellation()

        class CancellingSummarizer(_RecordingSummarizer):
            async def summarize(self, *, existing_summary, messages, cancellation):
                cancellation.cancel()
                cancellation.raise_if_cancelled()

        messages = (UserMessage("a" * 40), AssistantMessage("b" * 40), UserMessage("latest"))
        manager = CompactingContextManager(
            budget=ContextBudget(210),
            estimator=_LengthEstimator(),
            summarizer=CancellingSummarizer(),
            policy=CompactionPolicy(target_ratio=1),
            provider_default_reserve=0,
        )
        with pytest.raises(asyncio.CancelledError):
            await manager.prepare(_request(messages, configured=210), cancellation)

    asyncio.run(check())


def test_default_estimator_counts_static_tools_and_rejects_media() -> None:
    estimator = ConservativeTokenEstimator()
    plain = estimator.estimate(ModelContext("system", (MessageSegment(UserMessage("text")),), ()))
    assert plain.input_tokens > 0 and not plain.exact
    with pytest.raises(TokenEstimationError):
        estimator.estimate(
            ModelContext(None, (MessageSegment(UserMessage((ImageContent(BytesSource(b"x")),))),), ())
        )
