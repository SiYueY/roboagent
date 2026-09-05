"""Whole-request token budgeting and incremental context compaction."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Protocol

from roboagent.context.manager import (
    CompactionUpdate,
    ContextError,
    ContextManager,
    ContextRequest,
    ContextSummary,
    DefaultPromptRenderer,
    MessageSegment,
    ModelContext,
    PreparedContext,
    PromptRenderer,
    SummarySegment,
    _compose_prompt,
    _message_groups,
    _project_segments,
)
from roboagent.message import (
    AgentMessage,
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    FileContent,
    ImageContent,
    JsonContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
    canonical_json_dumps,
    canonical_message_digest,
    text_of,
)
from roboagent.model import ModelCapabilities, ModelSettings, Usage
from roboagent.runtime.types import CancellationToken


class ContextBudgetError(ContextError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class TokenEstimationError(ContextBudgetError):
    def __init__(self, reason: str) -> None:
        super().__init__("token_estimation_error", reason)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int | None = None
    reserve_tokens: int = 0

    def __post_init__(self) -> None:
        if self.max_tokens is not None and (
            not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool) or self.max_tokens < 1
        ):
            raise ValueError("max_tokens must be positive or None.")
        if not isinstance(self.reserve_tokens, int) or isinstance(self.reserve_tokens, bool) or self.reserve_tokens < 0:
            raise ValueError("reserve_tokens must be non-negative.")


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    input_tokens: int
    exact: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.input_tokens, int) or isinstance(self.input_tokens, bool) or self.input_tokens < 0:
            raise ValueError("input_tokens must be non-negative.")
        if not isinstance(self.exact, bool):
            raise TypeError("exact must be bool.")


class TokenEstimator(Protocol):
    def estimate(self, context: ModelContext) -> TokenEstimate: ...


class ConservativeTokenEstimator:
    """Provider-neutral text estimator with explicit failure for unknown media."""

    def estimate(self, context: ModelContext) -> TokenEstimate:
        characters = len(context.system_prompt or "")
        framing = 8 + len(context.segments) * 4 + len(context.tools) * 8
        for tool in context.tools:
            characters += len(tool.name) + len(tool.description) + len(canonical_json_dumps(tool.input_schema))
        for segment in context.segments:
            if isinstance(segment, SummarySegment):
                characters += len(segment.text) + 32
            elif isinstance(segment, MessageSegment):
                message = segment.message
                characters += 8
                for content in message.content:
                    if isinstance(content, TextContent):
                        characters += len(content.text)
                    elif isinstance(content, JsonContent):
                        characters += len(canonical_json_dumps(content.value))
                    elif isinstance(content, ArtifactReferenceContent):
                        characters += len(content.uri) + len(content.digest) + len(content.preview or "") + 32
                    elif isinstance(content, (ImageContent, AudioContent, FileContent)):
                        raise TokenEstimationError(f"unestimable_{type(content).__name__.lower()}")
                    else:  # pragma: no cover - closed MessageContent union
                        raise TokenEstimationError(f"unestimable_{type(content).__name__.lower()}")
                if isinstance(message, AssistantMessage):
                    for call in message.tool_calls:
                        characters += len(call.id) + len(call.name) + len(canonical_json_dumps(call.arguments))
                elif isinstance(message, ToolResultMessage) and message.error is not None:
                    characters += len(message.error.code) + len(message.error.message)
            else:
                characters += len(segment.uri) + len(segment.preview or "") + len(segment.media_type or "")
        return TokenEstimate(framing + math.ceil(characters / 4), exact=False)


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    target_ratio: float = 0.7
    min_recent_turns: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.target_ratio, (int, float)) or isinstance(self.target_ratio, bool) or not 0 < self.target_ratio <= 1:
            raise ValueError("target_ratio must be in (0, 1].")
        if not isinstance(self.min_recent_turns, int) or isinstance(self.min_recent_turns, bool) or self.min_recent_turns < 1:
            raise ValueError("min_recent_turns must be positive.")


@dataclass(frozen=True, slots=True)
class SummaryResult:
    text: str
    usage: Usage = Usage(0, 0, 0)
    summarizer_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("SummaryResult.text must be non-empty.")
        if not isinstance(self.usage, Usage):
            raise TypeError("SummaryResult.usage must be Usage.")


class ContextSummarizer(Protocol):
    async def summarize(
        self,
        *,
        existing_summary: ContextSummary | None,
        messages: tuple[AgentMessage, ...],
        cancellation: CancellationToken,
    ) -> SummaryResult: ...


class ExtractiveContextSummarizer:
    async def summarize(
        self,
        *,
        existing_summary: ContextSummary | None,
        messages: tuple[AgentMessage, ...],
        cancellation: CancellationToken,
    ) -> SummaryResult:
        cancellation.raise_if_cancelled()
        parts = [existing_summary.text] if existing_summary is not None else []
        for message in messages:
            text = text_of(message.content).strip()
            if text:
                parts.append(f"{message.role}: {text}")
            elif isinstance(message, AssistantMessage) and message.tool_calls:
                parts.append("assistant tools: " + ", ".join(call.name for call in message.tool_calls))
        result = "\n".join(parts).strip()
        if not result:
            result = "Earlier conversation contained no textual content."
        cancellation.raise_if_cancelled()
        return SummaryResult(result)


class CompactingContextManager(ContextManager):
    def __init__(
        self,
        *,
        budget: ContextBudget,
        estimator: TokenEstimator | None = None,
        summarizer: ContextSummarizer | None = None,
        policy: CompactionPolicy | None = None,
        renderer: PromptRenderer | None = None,
        provider_default_reserve: int = 1024,
        summary_format_version: int = 1,
        summarizer_budget: ContextBudget | None = None,
        summarizer_capabilities: ModelCapabilities | None = None,
        summarizer_settings: ModelSettings | None = None,
        summarizer_estimator: TokenEstimator | None = None,
    ) -> None:
        if not isinstance(budget, ContextBudget):
            raise TypeError("budget must be ContextBudget.")
        if not isinstance(provider_default_reserve, int) or isinstance(provider_default_reserve, bool) or provider_default_reserve < 0:
            raise ValueError("provider_default_reserve must be non-negative.")
        if not isinstance(summary_format_version, int) or isinstance(summary_format_version, bool) or summary_format_version < 1:
            raise ValueError("summary_format_version must be positive.")
        self.budget = budget
        self.estimator = estimator or ConservativeTokenEstimator()
        self.summarizer = summarizer or ExtractiveContextSummarizer()
        self.policy = policy or CompactionPolicy()
        self.renderer = renderer or DefaultPromptRenderer()
        self.provider_default_reserve = provider_default_reserve
        self.summary_format_version = summary_format_version
        self.summarizer_budget = summarizer_budget
        self.summarizer_capabilities = summarizer_capabilities
        self.summarizer_settings = summarizer_settings or ModelSettings()
        self.summarizer_estimator = summarizer_estimator or self.estimator

    async def prepare(self, request: ContextRequest, cancellation: CancellationToken) -> PreparedContext:
        cancellation.raise_if_cancelled()
        snapshot = request.snapshot
        base = await self.renderer.render(snapshot.prompt, cancellation)
        prompt = _compose_prompt(base, snapshot.skill_metadata)
        groups = _message_groups(snapshot.transcript)
        boundaries = _group_boundaries(groups)
        old = request.current_compaction
        valid = old is not None and self._valid_summary(old, snapshot.transcript, boundaries)
        current = old if valid else None
        clear_update = CompactionUpdate(None, old.source_digest) if old is not None and not valid else None
        context = ModelContext(prompt, _project_segments(snapshot.transcript, current), snapshot.tool_definitions)
        input_budget = _input_budget(self.budget, request.model_capabilities, request.model_settings, self.provider_default_reserve)
        if self._estimate(context) <= input_budget:
            return PreparedContext(context, Usage(0, 0, 0), clear_update)

        static = ModelContext(prompt, (), snapshot.tool_definitions)
        if self._estimate(static) > input_budget:
            raise ContextBudgetError("context_budget_exceeded", "static_overhead")

        tail_start = _minimum_tail_start(groups, self.policy.min_recent_turns)
        if tail_start == 0:
            raise ContextBudgetError("context_budget_exceeded", "minimum_retained_tail")
        for group in groups[tail_start:]:
            group_context = ModelContext(prompt, tuple(MessageSegment(item) for item in group), snapshot.tool_definitions)
            if self._estimate(group_context) > input_budget:
                raise ContextBudgetError("context_budget_exceeded", "atomic_group_too_large")

        old_end = current.source_end_exclusive if current is not None else 0
        candidate_ends = [boundary for boundary in boundaries if old_end < boundary <= sum(len(group) for group in groups[:tail_start])]
        target = max(1, int(input_budget * self.policy.target_ratio))
        placeholder = current.text if current is not None else "Earlier conversation summary."
        end = None
        for boundary in candidate_ends:
            projected = ModelContext(
                prompt,
                (SummarySegment(placeholder), *(MessageSegment(item) for item in snapshot.transcript[boundary:])),
                snapshot.tool_definitions,
            )
            if self._estimate(projected) <= target:
                end = boundary
                break
        if end is None:
            raise ContextBudgetError("context_budget_exceeded", "summary_and_minimum_tail")

        new_messages = snapshot.transcript[old_end:end]
        self._check_summarizer_input(current, new_messages)
        try:
            result = await self.summarizer.summarize(
                existing_summary=current,
                messages=new_messages,
                cancellation=cancellation,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ContextBudgetError("context_compaction_error", "summarizer_failure") from exc
        if not isinstance(result, SummaryResult):
            raise ContextBudgetError("context_compaction_error", "invalid_summary_result")
        cancellation.raise_if_cancelled()
        summary = ContextSummary(
            0,
            end,
            canonical_message_digest(snapshot.transcript[:end]),
            result.text,
            self.summary_format_version,
            result.summarizer_id,
        )
        projected = ModelContext(prompt, _project_segments(snapshot.transcript, summary), snapshot.tool_definitions)
        if self._estimate(projected) > target:
            raise ContextBudgetError("context_budget_exceeded", "summary_and_minimum_tail")
        expected = old.source_digest if old is not None else None
        return PreparedContext(projected, result.usage, CompactionUpdate(summary, expected))

    def _estimate(self, context: ModelContext) -> int:
        try:
            estimate = self.estimator.estimate(context)
        except TokenEstimationError:
            raise
        except Exception as exc:
            raise TokenEstimationError("estimator_failure") from exc
        if not isinstance(estimate, TokenEstimate):
            raise TokenEstimationError("invalid_estimate")
        return estimate.input_tokens

    def _valid_summary(
        self,
        summary: ContextSummary,
        transcript: tuple[AgentMessage, ...],
        boundaries: tuple[int, ...],
    ) -> bool:
        return (
            summary.summary_format_version == self.summary_format_version
            and summary.source_end_exclusive in boundaries
            and summary.source_end_exclusive <= len(transcript)
            and summary.source_digest == canonical_message_digest(transcript[: summary.source_end_exclusive])
        )

    def _check_summarizer_input(self, old: ContextSummary | None, messages: tuple[AgentMessage, ...]) -> None:
        if self.summarizer_budget is None:
            return
        capabilities = self.summarizer_capabilities or ModelCapabilities(context_window=self.summarizer_budget.max_tokens)
        budget = _input_budget(
            self.summarizer_budget,
            capabilities,
            self.summarizer_settings,
            self.provider_default_reserve,
        )
        segments = (
            *((SummarySegment(old.text),) if old is not None else ()),
            *(MessageSegment(message) for message in messages),
        )
        context = ModelContext("Summarize the following historical conversation context.", segments, ())
        try:
            estimate = self.summarizer_estimator.estimate(context)
        except Exception as exc:
            raise ContextBudgetError("context_compaction_error", "summarizer_token_estimation_failed") from exc
        if not isinstance(estimate, TokenEstimate):
            raise ContextBudgetError("context_compaction_error", "summarizer_token_estimation_failed")
        if estimate.input_tokens > budget:
            raise ContextBudgetError("context_compaction_error", "summarizer_input_too_large")


def _input_budget(
    budget: ContextBudget,
    capabilities: ModelCapabilities,
    settings: ModelSettings,
    provider_default_reserve: int,
) -> int:
    windows = [value for value in (budget.max_tokens, capabilities.context_window) if value is not None]
    if not windows:
        raise ContextBudgetError("context_budget_unavailable", "missing_context_window")
    effective = min(windows)
    reserve = max(budget.reserve_tokens, settings.max_output_tokens or provider_default_reserve)
    result = effective - reserve
    if result <= 0:
        raise ContextBudgetError("context_budget_invalid", "reserve_exhausts_window")
    return result


def _group_boundaries(groups: tuple[tuple[AgentMessage, ...], ...]) -> tuple[int, ...]:
    result = [0]
    for group in groups:
        result.append(result[-1] + len(group))
    return tuple(result)


def _minimum_tail_start(groups: tuple[tuple[AgentMessage, ...], ...], turns: int) -> int:
    seen = 0
    for index in range(len(groups) - 1, -1, -1):
        if isinstance(groups[index][0], UserMessage):
            seen += 1
            if seen == turns:
                return index
    return 0
