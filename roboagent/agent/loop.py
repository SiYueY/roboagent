"""Capability-neutral Runtime orchestration loop."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from roboagent.agent.hooks import HookDecision, ModelHookContext
from roboagent.agent.types import RunConfig
from roboagent.context import (
    ContextRequest,
    ContextSnapshot,
    ContextSummary,
    ModelContext,
    PreparedContext,
    SummarySegment,
)
from roboagent.message import AssistantMessage
from roboagent.model import (
    Model,
    ModelError,
    ModelProviderError,
    ModelResponse,
    ModelSettings,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageUpdated,
    collect_model_stream,
)
from roboagent.runtime.event import RunEventEmitter
from roboagent.runtime.execution import UsageContribution, UsageKnowledge
from roboagent.runtime.types import RunContext, RunError, RunPhase, ToolCallSummary
from roboagent.tool import (
    ToolBatchAborted,
    ToolBatchCancelled,
    ToolContext,
    ToolEffectRecord,
    ToolExecutor,
    committed_effects,
    result_message,
)

if TYPE_CHECKING:
    from roboagent.agent.agent import Agent
    from roboagent.agent.session import Session


class MaxTurnsError(Exception):
    def __init__(self, outcome: "LoopOutcome") -> None:
        self.outcome = outcome
        super().__init__("max_turns")


class RunCancelled(asyncio.CancelledError):
    def __init__(
        self,
        effects: tuple[ToolEffectRecord, ...] = (),
        output: AssistantMessage | None = None,
        usage: Usage | None = None,
        turns: int = 0,
    ) -> None:
        self.effects = effects
        self.output = output
        self.usage = usage
        self.turns = turns
        super().__init__("Run cancelled.")


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    output: AssistantMessage | None
    usage: Usage | None
    effects: tuple[ToolEffectRecord, ...]
    turns: int


HookInvoker = Callable[..., Awaitable[tuple[object, ...]]]
StateUpdater = Callable[..., None]
_PUBLIC_PROVIDER_ERROR_CODES = frozenset(
    {
        "provider_authentication_error",
        "provider_connection_error",
        "provider_http_error",
        "provider_rate_limit",
        "provider_timeout",
    }
)


async def run_loop(
    *,
    agent: "Agent",
    session: "Session",
    run_context: RunContext,
    config: RunConfig,
    events: RunEventEmitter,
    invoke_hooks: HookInvoker,
    update_state: StateUpdater,
    guidance_metadata: tuple[object, ...],
    tool_executor: ToolExecutor,
) -> LoopOutcome:
    progress = _LoopProgress()
    try:
        return await _run_loop_impl(
            agent=agent,
            session=session,
            run_context=run_context,
            config=config,
            events=events,
            invoke_hooks=invoke_hooks,
            update_state=update_state,
            guidance_metadata=guidance_metadata,
            tool_executor=tool_executor,
            progress=progress,
        )
    except RunCancelled:
        raise
    except asyncio.CancelledError as exc:
        raise RunCancelled(
            tuple(progress.effects), progress.output, progress.usage, progress.turns
        ) from exc


@dataclass(slots=True)
class _LoopProgress:
    output: AssistantMessage | None = None
    usage: Usage | None = None
    effects: list[ToolEffectRecord] = field(default_factory=list)
    turns: int = 0


async def _run_loop_impl(
    *,
    agent: "Agent",
    session: "Session",
    run_context: RunContext,
    config: RunConfig,
    events: RunEventEmitter,
    invoke_hooks: HookInvoker,
    update_state: StateUpdater,
    guidance_metadata: tuple[object, ...],
    tool_executor: ToolExecutor,
    progress: _LoopProgress,
) -> LoopOutcome:
    output: AssistantMessage | None = None
    usage: Usage | None = None
    effects = progress.effects
    for turn in range(1, config.max_turns + 1):
        progress.turns = turn
        run_context.cancellation.raise_if_cancelled()
        if turn > 1:
            await session.consume_pending(run_context.run_id, run_context.cancellation)
        update_state(RunPhase.PREPARING_CONTEXT, turn)
        try:
            while True:
                transcript, current_compaction = await session.capture_context_state(
                    run_context.run_id
                )
                request = ContextRequest(
                    snapshot=ContextSnapshot(
                        session_id=session.session_id,
                        transcript=transcript,
                        prompt=agent.prompt,
                        tool_definitions=agent.tool_registry.definitions(),
                        skill_metadata=guidance_metadata,
                    ),
                    model_settings=config.model_settings or ModelSettings(),
                    model_capabilities=agent.model.capabilities,
                    current_compaction=current_compaction,
                )
                prepared = await agent.context_manager.prepare(
                    request, run_context.cancellation
                )
                if not isinstance(prepared, PreparedContext):
                    raise TypeError("ContextManager must return PreparedContext.")
                _validate_prepared_summary(prepared, current_compaction)
                usage = _merge_usage(usage, prepared.usage_delta)
                progress.usage = usage
                if prepared.usage_delta is not None:
                    _contribute_usage(
                        run_context,
                        UsageContribution(UsageKnowledge.KNOWN, prepared.usage_delta),
                    )
                if prepared.compaction_update is None:
                    break
                if await session.commit_compaction(
                    run_context.run_id, prepared.compaction_update
                ):
                    summary = prepared.compaction_update.summary
                    payload: dict[str, object] = {
                        "outcome": "cleared" if summary is None else "updated"
                    }
                    if summary is not None:
                        payload.update(
                            source_end_exclusive=summary.source_end_exclusive,
                            source_digest=summary.source_digest,
                            summary_format_version=summary.summary_format_version,
                        )
                    await events.emit("context.compaction_completed", **payload)  # type: ignore[arg-type]
                    break
            model_context = prepared.model_context
            if not isinstance(model_context, ModelContext):
                raise TypeError("PreparedContext must contain ModelContext.")
            if model_context.recent_tail_complete is not prepared.recent_tail_complete:
                model_context = replace(
                    model_context,
                    recent_tail_complete=prepared.recent_tail_complete,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) == "context_compaction_error":
                await events.emit(
                    "context.compaction_failed", error_code="context_compaction_error"
                )
            raise _RunFailure(
                RunError(
                    getattr(exc, "code", "context_error"),
                    "Context preparation failed.",
                    cause_type=type(exc).__name__,
                ),
                tuple(effects),
                output,
                usage,
                turn,
            ) from exc
        hook_context = ModelHookContext(run_context, model_context)
        try:
            decisions = await invoke_hooks("before_model", hook_context)
        except _RunFailure as exc:
            await events.emit("model.failed", turn=turn, error_code="hook_error")
            raise _RunFailure(exc.error, tuple(effects), output, usage, turn) from exc
        if any(not isinstance(decision, HookDecision) for decision in decisions):
            await events.emit("model.failed", turn=turn, error_code="hook_error")
            raise _RunFailure(
                RunError(
                    "hook_error", "before_model hook returned an invalid decision."
                ),
                tuple(effects),
                output,
                usage,
                turn,
            )
        if any(decision is HookDecision.FAIL_RUN for decision in decisions):
            await events.emit("model.failed", turn=turn, error_code="hook_error")
            raise _RunFailure(
                RunError("hook_error", "before_model hook failed the Run."),
                tuple(effects),
                output,
                usage,
                turn,
            )
        run_context.cancellation.raise_if_cancelled()
        update_state(RunPhase.MODEL, turn)
        await events.emit("model.started", turn=turn)
        observed_usage: Usage | None = None
        try:

            async def observe_model(event: object, observed_turn: int = turn) -> None:
                nonlocal observed_usage
                if isinstance(event, TextDelta):
                    await events.emit(
                        "model.delta", turn=observed_turn, text=event.text
                    )
                elif isinstance(event, ToolCallStarted):
                    await events.emit(
                        "model.tool_call_started",
                        turn=observed_turn,
                        call_index=event.call_index,
                        tool_call_id=event.call_id,
                        tool_name=event.name,
                    )
                elif isinstance(event, ToolCallArgumentsDelta):
                    await events.emit(
                        "model.tool_call_arguments_delta",
                        turn=observed_turn,
                        call_index=event.call_index,
                        tool_call_id=event.call_id,
                        delta=event.delta,
                    )
                elif isinstance(event, ToolCallCompleted):
                    await events.emit(
                        "model.tool_call_completed",
                        turn=observed_turn,
                        call_index=event.call_index,
                        tool_call_id=event.call.id,
                        tool_name=event.call.name,
                    )
                elif isinstance(event, UsageUpdated):
                    observed_usage = event.usage

            response = await _collect_cancellable(
                agent.model,
                model_context,
                config.model_settings,
                observe_model,
                run_context,
            )
        except asyncio.CancelledError:
            _contribute_usage(
                run_context,
                UsageContribution(
                    UsageKnowledge.KNOWN
                    if observed_usage is not None
                    else UsageKnowledge.UNKNOWN,
                    observed_usage,
                ),
            )
            await events.emit("model.cancelled", turn=turn)
            raise
        except ModelError as exc:
            _contribute_usage(
                run_context,
                UsageContribution(
                    UsageKnowledge.KNOWN
                    if observed_usage is not None
                    else UsageKnowledge.UNKNOWN,
                    observed_usage,
                ),
            )
            await events.emit(
                "model.failed",
                turn=turn,
                error_code=getattr(exc, "code", "model_error"),
            )
            message = (
                str(exc)
                if isinstance(exc, ModelProviderError)
                and exc.code in _PUBLIC_PROVIDER_ERROR_CODES
                else "Model invocation failed."
            )
            raise _RunFailure(
                RunError(
                    getattr(exc, "code", "model_error"),
                    message,
                    cause_type=type(exc).__name__,
                ),
                tuple(effects),
                output,
                usage,
                turn,
            ) from exc
        except Exception as exc:
            _contribute_usage(
                run_context,
                UsageContribution(
                    UsageKnowledge.KNOWN
                    if observed_usage is not None
                    else UsageKnowledge.UNKNOWN,
                    observed_usage,
                ),
            )
            await events.emit("model.failed", turn=turn, error_code="model_error")
            raise _RunFailure(
                RunError(
                    "model_error",
                    "Model invocation failed.",
                    cause_type=type(exc).__name__,
                ),
                tuple(effects),
                output,
                usage,
                turn,
            ) from exc
        usage = _merge_usage(usage, response.usage)
        progress.usage = usage
        _contribute_usage(
            run_context,
            UsageContribution(
                UsageKnowledge.KNOWN
                if response.usage is not None
                else UsageKnowledge.UNKNOWN,
                response.usage,
            ),
        )
        await events.emit("model.completed", turn=turn)
        run_context.cancellation.raise_if_cancelled()
        try:
            await invoke_hooks("after_model", hook_context, response)
        except _RunFailure as exc:
            raise _RunFailure(exc.error, tuple(effects), output, usage, turn) from exc
        run_context.cancellation.raise_if_cancelled()
        output = response.message
        progress.output = output
        if not response.message.tool_calls:
            await session.commit_message(run_context.run_id, response.message)
            update_state(RunPhase.BETWEEN_TURNS, turn)
            if await session.pending_inputs():
                continue
            return LoopOutcome(output, usage, tuple(effects), turn)
        update_state(
            RunPhase.TOOL,
            turn,
            pending_tool_calls=tuple(
                ToolCallSummary(call.id, call.name)
                for call in response.message.tool_calls
            ),
        )
        try:
            assert run_context.execution is not None
            batch = await tool_executor.execute(
                response.message.tool_calls,
                ToolContext(
                    run_context.run_id,
                    run_context.session_id,
                    run_context.cancellation,
                    run_context.execution.tool_context(
                        tool_executor, run_context.session_id
                    ),
                ),
            )
        except ToolBatchCancelled as exc:
            effects.extend(exc.effects)
            raise RunCancelled(tuple(effects), output, usage, turn) from exc
        except ToolBatchAborted as exc:
            effects.extend(exc.effects)
            raise _RunFailure(exc.reason, tuple(effects), output, usage, turn) from exc
        try:
            run_context.cancellation.raise_if_cancelled()
        except asyncio.CancelledError:
            effects.extend(batch.effects)
            raise
        messages = tuple(result_message(result) for result in batch.results)
        try:
            await session.commit_exchange(
                run_context.run_id, response.message, messages
            )
        except Exception as exc:
            from roboagent.agent.persistence import SessionPersistenceError

            if isinstance(exc, SessionPersistenceError):
                effects.extend(committed_effects(batch.effects))
                assert run_context.execution is not None
                run_context.execution.mark_tool_calls_committed(
                    tuple(call.id for call in response.message.tool_calls)
                )
                raise _RunFailure(
                    RunError(
                        "session_persistence_error",
                        "Session persistence failed.",
                        cause_type=type(exc).__name__,
                    ),
                    tuple(effects),
                    output,
                    usage,
                    turn,
                ) from exc
            effects.extend(batch.effects)
            raise _RunFailure(
                RunError(
                    "transcript_commit_error",
                    "Tool exchange commit failed.",
                    cause_type=type(exc).__name__,
                ),
                tuple(effects),
                output,
                usage,
                turn,
            ) from exc
        final_effects = committed_effects(batch.effects)
        effects.extend(final_effects)
        assert run_context.execution is not None
        run_context.execution.mark_tool_calls_committed(
            tuple(call.id for call in response.message.tool_calls)
        )
        await events.emit(
            "tool_batch.committed",
            turn=turn,
            tool_call_ids=[call.id for call in response.message.tool_calls],
        )
        update_state(RunPhase.BETWEEN_TURNS, turn)
    raise MaxTurnsError(LoopOutcome(output, usage, tuple(effects), config.max_turns))


class _RunFailure(Exception):
    def __init__(
        self,
        error: RunError,
        effects: tuple[ToolEffectRecord, ...] = (),
        output: AssistantMessage | None = None,
        usage: Usage | None = None,
        turns: int = 0,
    ) -> None:
        self.error = error
        self.effects = effects
        self.output = output
        self.usage = usage
        self.turns = turns
        super().__init__(error.message)


def _validate_prepared_summary(
    prepared: PreparedContext, current_compaction: ContextSummary | None
) -> None:
    summaries = tuple(
        segment
        for segment in prepared.model_context.segments
        if isinstance(segment, SummarySegment)
    )
    update = prepared.compaction_update
    expected = update.summary if update is not None else current_compaction
    if expected is None:
        if summaries:
            raise TypeError("ModelContext cannot use an uncommitted SummarySegment.")
        return
    if len(summaries) > 1 or (
        summaries and summaries[0].text != getattr(expected, "text", None)
    ):
        raise TypeError(
            "ModelContext SummarySegment does not match Session compaction state."
        )


def _merge_usage(current: Usage | None, latest: Usage | None) -> Usage | None:
    if latest is None:
        return current
    if current is None:
        return latest

    def add(left: int | None, right: int | None) -> int | None:
        return None if left is None and right is None else (left or 0) + (right or 0)

    return Usage(
        add(current.input_tokens, latest.input_tokens),
        add(current.output_tokens, latest.output_tokens),
        add(current.total_tokens, latest.total_tokens),
    )


async def _collect_cancellable(
    model: Model,
    model_context: ModelContext,
    settings: ModelSettings | None,
    observer: Callable[[object], Awaitable[None]],
    run_context: RunContext,
) -> ModelResponse:
    task = asyncio.create_task(
        collect_model_stream(model, model_context, settings, observer)
    )
    cancelled = asyncio.create_task(run_context.cancellation.wait_cancelled())
    try:
        done, _ = await asyncio.wait(
            {task, cancelled}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancelled in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.CancelledError()
        return task.result()
    finally:
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)


def _contribute_usage(context: RunContext, usage: UsageContribution) -> None:
    execution = context.execution
    if execution is None:
        return
    execution.contribute_usage(usage)
