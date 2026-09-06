"""Canonical policy-aware ToolExecutor."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, replace
from time import monotonic
from typing import Awaitable, Protocol, Sequence, cast
from uuid import uuid4

from roboagent.message import (
    ArtifactReferenceContent,
    FrozenJsonObject,
    JsonContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    canonical_json_digest,
)
from roboagent.runtime.execution import (
    ChildRunExecutor,
    ChildRunRequest,
    ChildRunResult,
    ExecutionLineage,
    ExecutionRecordStatus,
    RuntimeToolExecutionContext,
    SettlementError,
    SupplementalExecutionRecord,
)
from roboagent.runtime.types import CancellationToken, RunError

from .tool import (
    AllowAllToolPolicy,
    CompositeToolOutcome,
    CompositeToolExecutionFailure,
    _CompositeToolCancellation,
    EffectCertainty,
    RawToolResult,
    Tool,
    ToolBatchAborted,
    ToolBatchResult,
    ToolContractError,
    ToolContext,
    ToolContent,
    ToolDecision,
    ToolEffectKind,
    ToolEffectReporting,
    ToolEffectRecord,
    ToolEffectStatus,
    ToolEffectUnknown,
    ToolErrorInfo,
    ToolExecutionMode,
    ToolExecutionFailure,
    ToolExecutionPolicy,
    ToolExecutionResult,
    ToolHandlerReturn,
    ToolPolicyDecision,
    ToolJsonContent,
    ToolRegistry,
    ToolTextContent,
    validate_tool_arguments,
)
from .approval import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalSettings,
)
from .materializer import (
    InlineToolResultMaterializer,
    ToolMaterializationError,
    ToolResultMaterializer,
    raw_result_evidence,
)


@dataclass(frozen=True, slots=True)
class ToolExecutorConfig:
    max_calls_per_turn: int = 32
    max_concurrency: int = 8
    default_timeout: float | None = 60.0
    cancellation_grace_period: float = 2.0
    max_error_chars: int = 2048

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (self.max_calls_per_turn, self.max_concurrency)
        ):
            raise ValueError("Tool call and concurrency limits must be positive.")
        if self.default_timeout is not None and (
            isinstance(self.default_timeout, bool)
            or not isinstance(self.default_timeout, (int, float))
            or self.default_timeout <= 0
        ):
            raise ValueError("default_timeout must be positive or None.")
        if (
            isinstance(self.cancellation_grace_period, bool)
            or not isinstance(self.cancellation_grace_period, (int, float))
            or self.cancellation_grace_period < 0
            or not isinstance(self.max_error_chars, int)
            or isinstance(self.max_error_chars, bool)
            or self.max_error_chars < 1
        ):
            raise ValueError("Invalid ToolExecutor limits.")


class RunEventEmitter(Protocol):
    async def emit(
        self,
        event_type: str,
        *,
        lineage: ExecutionLineage | None = None,
        **payload: object,
    ) -> object: ...


class ToolBatchCancelled(asyncio.CancelledError):
    def __init__(self, effects: tuple[ToolEffectRecord, ...]) -> None:
        self.effects = tuple(effects)
        if not all(isinstance(effect, ToolEffectRecord) for effect in self.effects):
            raise TypeError("ToolBatchCancelled effects must be canonical.")
        super().__init__("Tool batch cancelled.")


@dataclass(frozen=True, slots=True)
class _PreparedCall:
    call: ToolCall
    tool: Tool
    timeout: float | None


@dataclass(frozen=True, slots=True)
class _CancellationEvidence:
    effects: tuple[ToolEffectRecord, ...]
    records: tuple[SupplementalExecutionRecord, ...] = ()


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: ToolExecutionPolicy | None = None,
        hooks: Sequence[object] = (),
        events: RunEventEmitter | None = None,
        config: ToolExecutorConfig | None = None,
        hook_timeout: float | None = None,
        result_materializer: ToolResultMaterializer | None = None,
        approval_provider: ApprovalProvider | None = None,
        approval_settings: ApprovalSettings | None = None,
        child_executor: ChildRunExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or AllowAllToolPolicy()
        self.hooks = tuple(hooks)
        self.events = events
        self.config = config or ToolExecutorConfig()
        self.hook_timeout = hook_timeout
        self.result_materializer = result_materializer or InlineToolResultMaterializer()
        self.approval_provider = approval_provider
        self.approval_settings = approval_settings or ApprovalSettings()
        self.child_executor = child_executor

    async def execute(
        self, calls: tuple[ToolCall, ...], context: ToolContext
    ) -> ToolBatchResult:
        if len(calls) > self.config.max_calls_per_turn:
            raise ToolBatchAborted(
                RunError("max_calls_per_turn", "Tool call limit exceeded.")
            )
        context.cancellation.raise_if_cancelled()
        contexts = self._call_contexts(calls, context)
        tools = tuple(self.registry.get(call.name) for call in calls)
        concurrent = bool(calls) and all(
            tool is not None and tool.execution_mode is ToolExecutionMode.CONCURRENT
            for tool in tools
        )
        if not concurrent:
            results: list[ToolExecutionResult] = []
            effects: list[ToolEffectRecord] = []
            for index, (call, call_context) in enumerate(
                zip(calls, contexts, strict=True)
            ):
                try:
                    result, call_effects = await self._one(call, call_context)
                except ToolBatchAborted as exc:
                    await self._abort_unprocessed(
                        calls, contexts, index + 1, "batch_aborted"
                    )
                    raise ToolBatchAborted(
                        exc.reason, (*effects, *exc.effects)
                    ) from exc
                except ToolBatchCancelled as exc:
                    await self._abort_unprocessed(
                        calls, contexts, index + 1, "cancelled"
                    )
                    raise ToolBatchCancelled((*effects, *exc.effects)) from exc
                except asyncio.CancelledError as exc:
                    await self._abort_unprocessed(
                        calls, contexts, index + 1, "cancelled"
                    )
                    raise ToolBatchCancelled(tuple(effects)) from exc
                results.append(result)
                effects.extend(call_effects)
            return ToolBatchResult(calls, tuple(results), tuple(effects))
        return await self._concurrent(calls, contexts)

    async def _concurrent(
        self, calls: tuple[ToolCall, ...], contexts: tuple[ToolContext, ...]
    ) -> ToolBatchResult:
        results: list[ToolExecutionResult | None] = [None] * len(calls)
        effects: list[tuple[ToolEffectRecord, ...] | None] = [None] * len(calls)
        prepared: list[tuple[int, _PreparedCall, ToolContext]] = []
        try:
            for index, (call, context) in enumerate(zip(calls, contexts, strict=True)):
                value = await self._prepare(call, context)
                if isinstance(value, ToolExecutionResult):
                    results[index] = value
                    effects[index] = ()
                    await self._close_call_scope(context)
                else:
                    prepared.append((index, value, context))
        except ToolBatchAborted as exc:
            self._record_error(context, call, exc.reason.code)
            await self._abort_unprocessed(calls, contexts, 0, "batch_aborted")
            raise
        except BaseException:
            await self._abort_unprocessed(calls, contexts, 0, "cancelled")
            raise
        running: dict[
            asyncio.Task[tuple[ToolExecutionResult, tuple[ToolEffectRecord, ...]]], int
        ] = {}
        next_index = 0

        async def execute_prepared(
            item: _PreparedCall, context: ToolContext
        ) -> tuple[ToolExecutionResult, tuple[ToolEffectRecord, ...]]:
            try:
                return await self._execute_prepared(item, context)
            except ToolBatchAborted as exc:
                self._record_error(context, item.call, exc.reason.code)
                raise
            except ToolBatchCancelled:
                self._record_error(
                    context, item.call, "cancelled", ExecutionRecordStatus.CANCELLED
                )
                raise
            finally:
                await self._close_call_scope(context)

        def fill() -> None:
            nonlocal next_index
            while (
                next_index < len(prepared)
                and len(running) < self.config.max_concurrency
            ):
                index, item, context = prepared[next_index]
                running[asyncio.create_task(execute_prepared(item, context))] = index
                next_index += 1

        fill()
        try:
            while running:
                done, _ = await asyncio.wait(
                    running, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    index = running.pop(task)
                    try:
                        result, call_effects = task.result()
                    except ToolBatchAborted as exc:
                        cancelled_effects = await self._cancel_tasks(running)
                        await self._abort_unprocessed(
                            calls, contexts, 0, "batch_aborted"
                        )
                        raise ToolBatchAborted(
                            exc.reason,
                            (
                                *_flatten_effects(effects),
                                *exc.effects,
                                *cancelled_effects,
                            ),
                        ) from exc
                    except ToolBatchCancelled as exc:
                        cancelled_effects = await self._cancel_tasks(running)
                        await self._abort_unprocessed(calls, contexts, 0, "cancelled")
                        raise ToolBatchCancelled(
                            (
                                *_flatten_effects(effects),
                                *exc.effects,
                                *cancelled_effects,
                            )
                        ) from exc
                    results[index] = result
                    effects[index] = call_effects
                fill()
        except asyncio.CancelledError as exc:
            cancelled_effects = await self._cancel_tasks(running)
            await self._abort_unprocessed(calls, contexts, 0, "cancelled")
            raise ToolBatchCancelled(
                (*_flatten_effects(effects), *cancelled_effects)
            ) from exc
        return ToolBatchResult(
            calls,
            tuple(item for item in results if item is not None),
            tuple(effect for group in effects if group is not None for effect in group),
        )

    async def _cancel_tasks(
        self,
        running: dict[
            asyncio.Task[tuple[ToolExecutionResult, tuple[ToolEffectRecord, ...]]], int
        ],
    ) -> tuple[ToolEffectRecord, ...]:
        for task in running:
            task.cancel()
        settled = await asyncio.gather(*running, return_exceptions=True)
        effects: list[ToolEffectRecord] = []
        for value in settled:
            if isinstance(value, ToolBatchCancelled):
                effects.extend(value.effects)
            elif isinstance(value, ToolBatchAborted):
                effects.extend(value.effects)
            elif (
                isinstance(value, tuple)
                and len(value) == 2
                and isinstance(value[1], tuple)
            ):
                effects.extend(
                    item for item in value[1] if isinstance(item, ToolEffectRecord)
                )
        running.clear()
        return tuple(effects)

    async def _one(
        self, call: ToolCall, context: ToolContext
    ) -> tuple[ToolExecutionResult, tuple[ToolEffectRecord, ...]]:
        try:
            prepared = await self._prepare(call, context)
            if isinstance(prepared, ToolExecutionResult):
                return prepared, ()
            return await self._execute_prepared(prepared, context)
        except ToolBatchAborted as exc:
            self._record_error(context, call, exc.reason.code)
            raise
        except ToolBatchCancelled:
            self._record_error(
                context, call, "cancelled", ExecutionRecordStatus.CANCELLED
            )
            raise
        finally:
            await self._close_call_scope(context)

    async def _prepare(
        self, call: ToolCall, context: ToolContext
    ) -> _PreparedCall | ToolExecutionResult:
        context.cancellation.raise_if_cancelled()
        tool = self.registry.get(call.name)
        if tool is not None and (
            error := validate_tool_arguments(tool, call.arguments)
        ):
            result = ToolExecutionResult(
                call.id, call.name, error=self._bounded_error(error)
            )
            await self._emit_terminal("tool.failed", call, "invalid_arguments", context)
            await self._after_tool(context, result)
            self._record(context, call, result, ExecutionRecordStatus.FAILED)
            return result
        try:
            decision = await self.policy.evaluate(call, tool, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolBatchAborted(
                RunError(
                    "policy_error",
                    "Tool policy evaluation failed.",
                    cause_type=type(exc).__name__,
                )
            ) from exc
        if isinstance(decision, ToolDecision):
            decision = ToolPolicyDecision(decision)
        if not isinstance(decision, ToolPolicyDecision):
            raise ToolBatchAborted(
                RunError("policy_error", "Tool policy returned an invalid decision.")
            )
        if decision.action is ToolDecision.FAIL_RUN:
            await self._emit_terminal("tool.failed", call, "policy_fail_run", context)
            self._record_error(context, call, "policy_fail_run")
            raise ToolBatchAborted(
                RunError("policy_fail_run", "Tool policy failed the Run.")
            )
        if decision.action is ToolDecision.REJECT:
            result = self._error(call, "rejected", "Tool call rejected by policy.")
            await self._emit_terminal("tool.failed", call, "rejected", context)
            await self._after_tool(context, result)
            self._record(context, call, result, ExecutionRecordStatus.FAILED)
            return result
        if tool is None:
            result = self._error(call, "unknown_tool", "Unknown tool.")
            await self._emit_terminal("tool.failed", call, "unknown_tool", context)
            await self._after_tool(context, result)
            self._record(context, call, result, ExecutionRecordStatus.FAILED)
            return result
        if decision.action is ToolDecision.REQUIRE_APPROVAL:
            approval = await self._approval(call, tool, context, decision.reason)
            if approval is not None:
                await self._after_tool(context, approval)
                self._record(context, call, approval, ExecutionRecordStatus.FAILED)
                return approval
        try:
            timeout_resolver = getattr(tool, "requested_timeout", None)
            timeout = (
                timeout_resolver(call.arguments)
                if timeout_resolver is not None
                else None
            )
        except Exception as exc:
            raise ToolBatchAborted(
                RunError(
                    "tool_contract_error",
                    "Tool timeout resolution failed.",
                    cause_type=type(exc).__name__,
                )
            ) from exc
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ToolBatchAborted(
                RunError("tool_contract_error", "Tool timeout resolution was invalid.")
            )
        timeout = (
            timeout
            if timeout is not None
            else tool.timeout
            if tool.timeout is not None
            else self.config.default_timeout
        )
        if (
            isinstance(context.execution, RuntimeToolExecutionContext)
            and timeout is not None
        ):
            context.execution.cap_deadline(monotonic() + timeout)
        return _PreparedCall(call, tool, timeout)

    async def _execute_prepared(
        self,
        prepared: _PreparedCall,
        context: ToolContext,
    ) -> tuple[ToolExecutionResult, tuple[ToolEffectRecord, ...]]:
        call, tool, timeout = prepared.call, prepared.tool, prepared.timeout
        try:
            await self._before_tool(context, call)
        except ToolBatchAborted:
            await self._emit_terminal("tool.failed", call, "hook_error", context)
            self._record_error(context, call, "hook_error")
            raise
        context.cancellation.raise_if_cancelled()
        await self._emit("tool.started", call, context=context)
        task = asyncio.create_task(tool.execute(call.arguments, context))
        cancelled = asyncio.create_task(context.cancellation.wait_cancelled())
        try:
            waiters: set[asyncio.Task[object]] = {task, cancelled}  # type: ignore[arg-type]
            done, _ = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done:
                cancellation_evidence = await self._cancel_started(
                    task, tool, call, context
                )
                call_effects = cancellation_evidence.effects
                self._contribute_cancelled(context, tool, cancellation_evidence)
                self._record_error(
                    context, call, "cancelled", ExecutionRecordStatus.CANCELLED
                )
                await self._emit(
                    "tool.cancelled",
                    call,
                    context=context,
                    status="cancelled",
                    error_code="cancelled",
                )
                raise ToolBatchCancelled(call_effects)
            if task not in done:
                cancellation_evidence = await self._cancel_started(
                    task, tool, call, context, timed_out=True
                )
                call_effects = cancellation_evidence.effects
                result = ToolExecutionResult(
                    call.id,
                    call.name,
                    error=ToolErrorInfo("timeout", "Tool execution timed out."),
                )
                self._contribute_cancelled(context, tool, cancellation_evidence)
                await self._emit_terminal("tool.failed", call, "timeout", context)
                await self._after_with_effect(context, result, call_effects)
                self._record(context, call, result, ExecutionRecordStatus.FAILED)
                return result, call_effects
            returned = task.result()
            if isinstance(returned, CompositeToolOutcome):
                if tool.effect_reporting is not ToolEffectReporting.COMPOSITE:
                    raise ToolContractError("Leaf Tool returned CompositeToolOutcome.")
                content = self._validate_composite_content(returned)
                returned_effects = self._identity_effects(context, returned.effects)
                self._contribute_composite(context, returned_effects, returned.records)
                result = ToolExecutionResult(call.id, call.name, content=content)
                await self._emit_terminal("tool.completed", call, None, context)
                await self._after_with_effect(context, result, returned_effects)
                self._record(context, call, result, ExecutionRecordStatus.SUCCEEDED)
                return result, returned_effects
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                raise ToolContractError(
                    "Composite Tool must return CompositeToolOutcome."
                )
            raw = (
                returned
                if isinstance(returned, RawToolResult)
                else RawToolResult((returned,))
            )
            evidence = raw_result_evidence(raw)
            effect = self._effect(
                context, call, tool, ToolEffectStatus.SUCCEEDED, content=evidence
            )
            self._contribute_effects(context, (effect,))
            try:
                content = await self.result_materializer.materialize(
                    raw,
                    call=call,
                    context=context,
                    cancellation=context.cancellation,
                )
            except asyncio.CancelledError as exc:
                raise ToolBatchCancelled((effect,)) from exc
            except ToolMaterializationError as exc:
                await self._emit_terminal("tool.failed", call, exc.code, context)
                self._record_error(context, call, exc.code)
                raise ToolBatchAborted(
                    RunError(
                        exc.code,
                        "Tool result materialization failed.",
                        cause_type=type(exc).__name__,
                    ),
                    (effect,),
                ) from exc
            except Exception as exc:
                await self._emit_terminal(
                    "tool.failed", call, "tool_materialization_error", context
                )
                self._record_error(context, call, "tool_materialization_error")
                raise ToolBatchAborted(
                    RunError(
                        "tool_materialization_error",
                        "Tool result materialization failed.",
                        cause_type=type(exc).__name__,
                    ),
                    (effect,),
                ) from exc
            result = ToolExecutionResult(call.id, call.name, content=content)
            await self._emit_terminal("tool.completed", call, None, context)
            await self._after_with_effect(context, result, (effect,))
            self._record(context, call, result, ExecutionRecordStatus.SUCCEEDED)
            return result, (effect,)
        except ToolBatchCancelled:
            raise
        except ToolBatchAborted:
            raise
        except _CompositeToolCancellation as exc:
            if tool.effect_reporting is not ToolEffectReporting.COMPOSITE:
                raise ToolContractError(
                    "Leaf Tool raised composite cancellation evidence."
                ) from exc
            effects = self._identity_effects(context, exc.effects)
            self._contribute_composite(context, effects, exc.records)
            self._record_error(
                context, call, "cancelled", ExecutionRecordStatus.CANCELLED
            )
            await self._emit(
                "tool.cancelled",
                call,
                context=context,
                status="cancelled",
                error_code="cancelled",
            )
            raise ToolBatchCancelled(effects) from exc
        except ToolEffectUnknown as exc:
            error = self._bounded_error(exc.error)
            result = ToolExecutionResult(call.id, call.name, error=error)
            effect = self._effect(
                context, call, tool, ToolEffectStatus.UNKNOWN, error=error
            )
            self._contribute_effects(context, (effect,))
            await self._emit_terminal("tool.failed", call, error.code, context)
            await self._after_with_effect(context, result, (effect,))
            self._record(context, call, result, ExecutionRecordStatus.UNKNOWN)
            return result, (effect,)
        except CompositeToolExecutionFailure as exc:
            error = self._bounded_error(exc.error)
            result = ToolExecutionResult(call.id, call.name, error=error)
            effects = self._identity_effects(context, exc.effects)
            self._contribute_composite(context, effects, exc.records)
            await self._emit_terminal("tool.failed", call, error.code, context)
            await self._after_with_effect(context, result, effects)
            self._record(
                context,
                call,
                result,
                ExecutionRecordStatus.UNKNOWN
                if effects
                else ExecutionRecordStatus.FAILED,
            )
            return result, effects
        except ToolExecutionFailure as exc:
            error = self._bounded_error(exc.error)
            result = ToolExecutionResult(call.id, call.name, error=error)
            effects = (
                ()
                if tool.effect_reporting is ToolEffectReporting.COMPOSITE
                else (
                    self._effect(
                        context, call, tool, ToolEffectStatus.FAILED, error=error
                    ),
                )
            )
            self._contribute_effects(context, effects)
            await self._emit_terminal("tool.failed", call, error.code, context)
            await self._after_with_effect(context, result, effects)
            self._record(context, call, result, ExecutionRecordStatus.FAILED)
            return result, effects
        except ToolContractError as exc:
            composite_error = str(exc) == "invalid_composite_tool_content"
            error = self._bounded_error(
                ToolErrorInfo(
                    "invalid_composite_tool_content"
                    if composite_error
                    else "invalid_tool_output",
                    "Composite Tool returned invalid canonical content."
                    if composite_error
                    else "Tool returned non-canonical output.",
                )
            )
            status = (
                ToolEffectStatus.UNKNOWN
                if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING
                else ToolEffectStatus.FAILED
            )
            effects = (
                ()
                if tool.effect_reporting is ToolEffectReporting.COMPOSITE
                else (self._effect(context, call, tool, status, error=error),)
            )
            self._contribute_effects(context, effects)
            await self._emit_terminal("tool.failed", call, error.code, context)
            self._record_error(
                context,
                call,
                error.code,
                ExecutionRecordStatus.UNKNOWN
                if status is ToolEffectStatus.UNKNOWN
                else ExecutionRecordStatus.FAILED,
            )
            raise ToolBatchAborted(
                RunError(
                    "tool_contract_error",
                    "Tool violated its output contract.",
                    cause_type=type(exc).__name__,
                ),
                effects,
            ) from exc
        except SettlementError as exc:
            self._record_error(context, call, exc.code, ExecutionRecordStatus.UNKNOWN)
            await self._emit_terminal("tool.failed", call, exc.code, context)
            raise ToolBatchAborted(
                RunError(
                    exc.code, "Tool settlement failed.", cause_type=type(exc).__name__
                )
            ) from exc
        except asyncio.CancelledError as exc:
            cancellation_evidence = await self._cancel_started(
                task, tool, call, context
            )
            effects = cancellation_evidence.effects
            self._contribute_cancelled(context, tool, cancellation_evidence)
            self._record_error(
                context, call, "cancelled", ExecutionRecordStatus.CANCELLED
            )
            await self._emit(
                "tool.cancelled",
                call,
                context=context,
                status="cancelled",
                error_code="cancelled",
            )
            raise ToolBatchCancelled(effects) from exc
        except Exception:
            error = self._bounded_error(
                ToolErrorInfo("execution_error", "Tool execution failed.")
            )
            result = ToolExecutionResult(call.id, call.name, error=error)
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                await self._emit_terminal(
                    "tool.failed", call, "execution_error", context
                )
                await self._after_with_effect(context, result, ())
                self._record(context, call, result, ExecutionRecordStatus.UNKNOWN)
                return result, ()
            if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
                effect_error = self._bounded_error(
                    ToolErrorInfo(
                        "effect_unknown", "Tool side effect could not be determined."
                    )
                )
                effect = self._effect(
                    context, call, tool, ToolEffectStatus.UNKNOWN, error=effect_error
                )
            else:
                effect = self._effect(
                    context, call, tool, ToolEffectStatus.FAILED, error=error
                )
            self._contribute_effects(context, (effect,))
            await self._emit_terminal("tool.failed", call, "execution_error", context)
            await self._after_with_effect(context, result, (effect,))
            self._record(
                context,
                call,
                result,
                ExecutionRecordStatus.UNKNOWN
                if effect.status is ToolEffectStatus.UNKNOWN
                else ExecutionRecordStatus.FAILED,
            )
            return result, (effect,)
        finally:
            cancelled.cancel()
            cancelled.add_done_callback(_consume_task_result)

    async def _cancel_started(
        self,
        task: asyncio.Task[ToolHandlerReturn],
        tool: Tool,
        call: ToolCall,
        context: ToolContext,
        *,
        timed_out: bool = False,
    ) -> _CancellationEvidence:
        from roboagent.runtime import (
            CancellationOrigin,
            CancellationReason,
            RuntimeCancellation,
        )

        if isinstance(context.execution, RuntimeToolExecutionContext) and isinstance(
            context.cancellation, RuntimeCancellation
        ):
            context.cancellation.cancel(
                CancellationReason.TIMEOUT if timed_out else CancellationReason.USER,
                CancellationOrigin.RUNTIME,
            )
        task.cancel()
        try:
            settlement_active = (
                isinstance(context.execution, RuntimeToolExecutionContext)
                and context.execution.settlement_active
            )
            if settlement_active:
                returned = await asyncio.shield(task)
            else:
                returned = await asyncio.wait_for(
                    asyncio.shield(task), self.config.cancellation_grace_period
                )
        except TimeoutError:
            task.cancel()
            task.add_done_callback(_consume_task_result)
        except _CompositeToolCancellation as exc:
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                return _CancellationEvidence(
                    self._identity_effects(context, exc.effects), exc.records
                )
            return _CancellationEvidence(())
        except CompositeToolExecutionFailure as exc:
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                return _CancellationEvidence(
                    self._identity_effects(context, exc.effects), exc.records
                )
            return _CancellationEvidence(
                (
                    self._effect(
                        context,
                        call,
                        tool,
                        ToolEffectStatus.FAILED,
                        error=self._bounded_error(exc.error),
                    ),
                )
            )
        except ToolEffectUnknown as exc:
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                return _CancellationEvidence(())
            return _CancellationEvidence(
                (
                    self._effect(
                        context,
                        call,
                        tool,
                        ToolEffectStatus.UNKNOWN,
                        error=self._bounded_error(exc.error),
                    ),
                )
            )
        except ToolExecutionFailure as exc:
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                return _CancellationEvidence(())
            return _CancellationEvidence(
                (
                    self._effect(
                        context,
                        call,
                        tool,
                        ToolEffectStatus.FAILED,
                        error=self._bounded_error(exc.error),
                    ),
                )
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
                return _CancellationEvidence(())
            if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
                error = ToolErrorInfo(
                    "effect_unknown", "Tool side effect could not be determined."
                )
                return _CancellationEvidence(
                    (
                        self._effect(
                            context, call, tool, ToolEffectStatus.UNKNOWN, error=error
                        ),
                    )
                )
            error = ToolErrorInfo(
                "execution_error", "Tool execution failed during cancellation."
            )
            return _CancellationEvidence(
                (
                    self._effect(
                        context, call, tool, ToolEffectStatus.FAILED, error=error
                    ),
                )
            )
        else:
            if isinstance(returned, CompositeToolOutcome):
                if tool.effect_reporting is not ToolEffectReporting.COMPOSITE:
                    return _CancellationEvidence(())
                return _CancellationEvidence(
                    self._identity_effects(context, returned.effects), returned.records
                )
            raw = (
                returned
                if isinstance(returned, RawToolResult)
                else RawToolResult((returned,))
            )
            evidence = raw_result_evidence(raw)
            return _CancellationEvidence(
                (
                    self._effect(
                        context,
                        call,
                        tool,
                        ToolEffectStatus.SUCCEEDED,
                        content=evidence,
                    ),
                )
            )
        if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
            return _CancellationEvidence(())
        if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
            error = ToolErrorInfo(
                "timeout" if timed_out else "effect_unknown",
                "Tool execution timed out; its side effect could not be determined."
                if timed_out
                else "Tool side effect could not be determined.",
            )
            return _CancellationEvidence(
                (
                    self._effect(
                        context, call, tool, ToolEffectStatus.UNKNOWN, error=error
                    ),
                )
            )
        if timed_out:
            error = ToolErrorInfo("timeout", "Tool execution timed out.")
            status = ToolEffectStatus.TIMED_OUT
        else:
            error = ToolErrorInfo("cancelled", "Tool execution was cancelled.")
            status = ToolEffectStatus.CANCELLED
        return _CancellationEvidence(
            (self._effect(context, call, tool, status, error=error),)
        )

    async def _approval(
        self,
        call: ToolCall,
        tool: Tool,
        context: ToolContext,
        reason: str | None,
    ) -> ToolExecutionResult | None:
        request = ApprovalRequest(
            uuid4().hex,
            context.run_id,
            context.session_id,
            call.id,
            call.name,
            call.arguments,
            canonical_json_digest(call.arguments),
            reason,
            context.execution.lineage if context.execution is not None else None,
            tool.effect_kind.value,
        )
        await self._emit(
            "approval.requested",
            call,
            context=context,
            approval_id=request.approval_id,
            arguments_digest=request.arguments_digest,
        )
        if self.approval_provider is None:
            await self._emit_approval_resolved(
                request, call, context, outcome="error", error_code="approval_error"
            )
            raise ToolBatchAborted(
                RunError(
                    "approval_error",
                    "Tool approval is required but no provider is configured.",
                )
            )
        task = asyncio.create_task(
            self.approval_provider.request(request, context.cancellation)
        )
        cancelled = asyncio.create_task(context.cancellation.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {task, cancelled},
                timeout=self.approval_settings.timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await self._emit_approval_resolved(
                    request, call, context, outcome="cancelled", error_code="cancelled"
                )
                raise asyncio.CancelledError()
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                context.cancellation.raise_if_cancelled()
                result = self._error(
                    call, "approval_timeout", "Tool approval timed out."
                )
                await self._emit_approval_resolved(
                    request,
                    call,
                    context,
                    outcome="timed_out",
                    error_code="approval_timeout",
                )
                await self._emit_terminal(
                    "tool.failed", call, "approval_timeout", context
                )
                return result
            try:
                response = task.result()
            except asyncio.CancelledError as exc:
                context.cancellation.raise_if_cancelled()
                await self._emit_approval_resolved(
                    request, call, context, outcome="error", error_code="approval_error"
                )
                raise ToolBatchAborted(
                    RunError(
                        "approval_error", "Approval provider cancelled unexpectedly."
                    )
                ) from exc
            except Exception as exc:
                await self._emit_approval_resolved(
                    request, call, context, outcome="error", error_code="approval_error"
                )
                raise ToolBatchAborted(
                    RunError(
                        "approval_error",
                        "Approval provider failed.",
                        cause_type=type(exc).__name__,
                    )
                ) from exc
            if not isinstance(response, ApprovalResponse):
                await self._emit_approval_resolved(
                    request, call, context, outcome="error", error_code="approval_error"
                )
                raise ToolBatchAborted(
                    RunError(
                        "approval_error",
                        "Approval provider returned an invalid response.",
                    )
                )
            if (
                response.approval_id != request.approval_id
                or response.arguments_digest != request.arguments_digest
            ):
                await self._emit_approval_resolved(
                    request,
                    call,
                    context,
                    outcome="mismatch",
                    error_code="approval_mismatch",
                )
                raise ToolBatchAborted(
                    RunError(
                        "approval_mismatch",
                        "Approval response did not match the ToolCall.",
                    )
                )
            if response.decision is ApprovalDecision.REJECT:
                result = self._error(
                    call, "approval_rejected", "Tool call was not approved."
                )
                await self._emit_approval_resolved(
                    request,
                    call,
                    context,
                    outcome="rejected",
                    error_code="approval_rejected",
                )
                await self._emit_terminal(
                    "tool.failed", call, "approval_rejected", context
                )
                return result
            await self._emit_approval_resolved(
                request, call, context, outcome="approved", error_code=None
            )
            return None
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _before_tool(self, context: ToolContext, call: ToolCall) -> None:
        from roboagent.agent.hooks import HookDecision, ToolHookContext
        from roboagent.runtime.types import RunContext

        hook_context = ToolHookContext(
            RunContext(
                context.run_id,
                context.session_id,
                context.cancellation,
                context.execution,
            )
        )
        for hook in self.hooks:
            callback = getattr(hook, "before_tool", None)
            if callback is None:
                continue
            try:
                decision = callback(hook_context, call)
                if not inspect.isawaitable(decision):
                    raise TypeError("before_tool hook must be async.")
                decision = await self._await_hook(decision, context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ToolBatchAborted(
                    RunError(
                        "hook_error",
                        "before_tool hook failed.",
                        cause_type=type(exc).__name__,
                    )
                ) from exc
            if not isinstance(decision, HookDecision):
                raise ToolBatchAborted(
                    RunError(
                        "hook_error", "before_tool hook returned an invalid decision."
                    )
                )
            if getattr(decision, "value", decision) == "fail_run":
                raise ToolBatchAborted(
                    RunError("hook_error", "before_tool hook failed the Run.")
                )

    async def _after_tool(
        self, context: ToolContext, result: ToolExecutionResult
    ) -> None:
        from roboagent.agent.hooks import ToolHookContext
        from roboagent.runtime.types import RunContext

        context.cancellation.raise_if_cancelled()
        hook_context = ToolHookContext(
            RunContext(
                context.run_id,
                context.session_id,
                context.cancellation,
                context.execution,
            )
        )
        for hook in self.hooks:
            callback = getattr(hook, "after_tool", None)
            if callback is None:
                continue
            try:
                value = callback(hook_context, result)
                if not inspect.isawaitable(value):
                    raise TypeError("after_tool hook must be async.")
                value = await self._await_hook(value, context)
                if value is not None:
                    raise TypeError("after_tool hook must return None.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ToolBatchAborted(
                    RunError(
                        "hook_error",
                        "after_tool hook failed.",
                        cause_type=type(exc).__name__,
                    )
                ) from exc

    async def _after_with_effect(
        self,
        context: ToolContext,
        result: ToolExecutionResult,
        effects: tuple[ToolEffectRecord, ...],
    ) -> None:
        try:
            await self._after_tool(context, result)
        except asyncio.CancelledError as exc:
            raise ToolBatchCancelled(effects) from exc
        except ToolBatchAborted as exc:
            raise ToolBatchAborted(exc.reason, effects) from exc

    async def _await_hook(self, awaitable: object, context: ToolContext) -> object:
        task = asyncio.ensure_future(cast(Awaitable[object], awaitable))
        cancelled = asyncio.create_task(context.cancellation.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {task, cancelled},
                timeout=self.hook_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError()
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                context.cancellation.raise_if_cancelled()
                raise TimeoutError("Hook timed out.")
            return task.result()
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    def _error(self, call: ToolCall, code: str, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call.id, call.name, error=self._bounded_error(ToolErrorInfo(code, message))
        )

    def _bounded_error(self, error: ToolErrorInfo) -> ToolErrorInfo:
        if len(error.message) <= self.config.max_error_chars:
            return error
        limit = self.config.max_error_chars
        message = "…" if limit == 1 else error.message[: limit - 1] + "…"
        return ToolErrorInfo(error.code, message, error.retryable)

    def _call_contexts(
        self, calls: tuple[ToolCall, ...], context: ToolContext
    ) -> tuple[ToolContext, ...]:
        execution = context.execution
        if not isinstance(execution, RuntimeToolExecutionContext):
            return tuple(context for _ in calls)
        return tuple(
            ToolContext(
                context.run_id,
                context.session_id,
                child.cancellation,
                child,
            )
            for child in (
                execution.child_tool_context(call, self, context.session_id)
                for call in calls
            )
        )

    async def execute_nested(
        self,
        call: ToolCall,
        parent_execution: object,
        session_id: str = "nested",
    ) -> ToolExecutionResult:
        if not isinstance(parent_execution, RuntimeToolExecutionContext):
            return ToolExecutionResult(
                call.id,
                call.name,
                error=ToolErrorInfo(
                    "nested_execution_unavailable", "Nested execution is unavailable."
                ),
            )
        parent = ToolContext(
            parent_execution.lineage.execution_run_id,
            session_id,
            cast(CancellationToken, parent_execution.cancellation),
            parent_execution,
        )
        batch = await self.execute((call,), parent)
        return batch.results[0]

    def validate_nested(self, call: ToolCall) -> ToolExecutionResult | None:
        tool = self.registry.get(call.name)
        if tool is None:
            return self._error(call, "unknown_tool", "Unknown tool.")
        error = validate_tool_arguments(tool, call.arguments)
        if error is not None:
            return ToolExecutionResult(
                call.id, call.name, error=self._bounded_error(error)
            )
        return None

    async def run_child(
        self, request: ChildRunRequest, parent: RuntimeToolExecutionContext
    ) -> ChildRunResult:
        if self.child_executor is None:
            from roboagent.runtime import ExecutionRequestError

            raise ExecutionRequestError(
                "nested_execution_unavailable", "Child Agent execution is unavailable."
            )
        return await self.child_executor.run_child(request, parent)

    async def _close_call_scope(self, context: ToolContext) -> None:
        execution = context.execution
        if not isinstance(execution, RuntimeToolExecutionContext):
            return
        await execution.close_tool_scope()

    async def _abort_unprocessed(
        self,
        calls: tuple[ToolCall, ...],
        contexts: tuple[ToolContext, ...],
        start: int,
        code: str,
    ) -> None:
        for call, context in zip(calls[start:], contexts[start:], strict=True):
            self._record_error(context, call, code, ExecutionRecordStatus.CANCELLED)
            await self._close_call_scope(context)

    def _effect(
        self,
        context: ToolContext,
        call: ToolCall,
        tool: Tool,
        status: ToolEffectStatus,
        *,
        content: ToolContent | None = None,
        error: ToolErrorInfo | None = None,
    ) -> ToolEffectRecord:
        effect_id = None
        if isinstance(context.execution, RuntimeToolExecutionContext):
            effect_id = context.execution.next_effect_id()
        certainty = (
            EffectCertainty.CERTAIN
            if status is ToolEffectStatus.SUCCEEDED
            else EffectCertainty.UNKNOWN
            if status is ToolEffectStatus.UNKNOWN
            else EffectCertainty.CERTAIN_NO_EFFECT
        )
        return ToolEffectRecord(
            call.id,
            call.name,
            tool.effect_kind,
            status,
            content=content,
            error=error,
            effect_id=effect_id,
            certainty=certainty,
        )

    def _identity_effects(
        self, context: ToolContext, effects: tuple[ToolEffectRecord, ...]
    ) -> tuple[ToolEffectRecord, ...]:
        if not isinstance(context.execution, RuntimeToolExecutionContext):
            return effects
        identified: list[ToolEffectRecord] = []
        seen = set()
        for effect in effects:
            if not isinstance(effect, ToolEffectRecord):
                raise ToolContractError("Composite Tool returned invalid effects.")
            value = (
                effect
                if effect.effect_id is not None
                else replace(effect, effect_id=context.execution.next_effect_id())
            )
            if (
                value.effect_id is not None
                and value.effect_id.scope_id
                != context.execution.lineage.scope_id
            ):
                raise ToolContractError(
                    "Composite Tool returned an effect owned by another scope."
                )
            if value.effect_id in seen:
                continue
            seen.add(value.effect_id)
            identified.append(value)
        return tuple(identified)

    def _contribute_effects(
        self, context: ToolContext, effects: tuple[ToolEffectRecord, ...]
    ) -> None:
        if not effects or not isinstance(
            context.execution, RuntimeToolExecutionContext
        ):
            return
        context.execution.contribute_effects(effects)

    def _contribute_composite(self, context: ToolContext, effects, records) -> None:
        if not isinstance(context.execution, RuntimeToolExecutionContext):
            return
        if not all(hasattr(record, "status") for record in records):
            raise ToolContractError("Composite Tool returned invalid records.")
        context.execution.contribute_composite(
            tuple(effects), tuple(records)
        )

    def _contribute_cancelled(
        self,
        context: ToolContext,
        tool: Tool,
        evidence: _CancellationEvidence,
    ) -> None:
        if tool.effect_reporting is ToolEffectReporting.COMPOSITE:
            self._contribute_composite(context, evidence.effects, evidence.records)
        else:
            self._contribute_effects(context, evidence.effects)

    def _validate_composite_content(
        self, outcome: CompositeToolOutcome
    ) -> tuple[ToolContent, ...]:
        content = tuple(outcome.content)
        if not all(
            isinstance(
                item, (ToolTextContent, ToolJsonContent, ArtifactReferenceContent)
            )
            for item in content
        ):
            raise ToolContractError("invalid_composite_tool_content")
        return content

    def _record(
        self,
        context: ToolContext,
        call: ToolCall,
        result: ToolExecutionResult,
        status: ExecutionRecordStatus,
    ) -> None:
        execution = context.execution
        if not isinstance(execution, RuntimeToolExecutionContext):
            return
        tool = self.registry.get(call.name)
        preview: object = call.arguments
        redactor = getattr(tool, "record_redactor", None)
        if redactor is not None:
            try:
                preview = redactor(call.arguments)
            except Exception:
                preview = None
        canonical_preview = preview if isinstance(preview, FrozenJsonObject) else None
        evidence: FrozenJsonObject | None = None
        if result.error is not None:
            evidence = FrozenJsonObject(
                {"error": result.error.message, "retryable": result.error.retryable}
            )
        elif result.content is not None:
            evidence = FrozenJsonObject({"content_blocks": len(result.content)})
        execution.record_tool_call(
            call=call,
            arguments_preview=canonical_preview,
            status=status,
            error_code=result.error.code if result.error else None,
            evidence=evidence,
        )

    def _record_error(
        self,
        context: ToolContext,
        call: ToolCall,
        code: str,
        status: ExecutionRecordStatus = ExecutionRecordStatus.FAILED,
    ) -> None:
        self._record(
            context,
            call,
            ToolExecutionResult(
                call.id,
                call.name,
                error=ToolErrorInfo(code, "Tool execution did not complete."),
            ),
            status,
        )

    async def _emit(
        self,
        event_type: str,
        call: ToolCall,
        *,
        context: ToolContext | None = None,
        **payload: object,
    ) -> None:
        if self.events:
            lineage = (
                context.execution.lineage
                if context is not None and context.execution is not None
                else None
            )
            await self.events.emit(
                event_type,
                lineage=lineage,
                tool_call_id=call.id,
                tool_name=call.name,
                **payload,
            )

    async def _emit_terminal(
        self,
        event_type: str,
        call: ToolCall,
        error_code: str | None,
        context: ToolContext | None = None,
    ) -> None:
        await self._emit(event_type, call, context=context, error_code=error_code)

    async def _emit_approval_resolved(
        self,
        request: ApprovalRequest,
        call: ToolCall,
        context: ToolContext,
        *,
        outcome: str,
        error_code: str | None,
    ) -> None:
        await self._emit(
            "approval.resolved",
            call,
            context=context,
            approval_id=request.approval_id,
            arguments_digest=request.arguments_digest,
            outcome=outcome,
            error_code=error_code,
        )


def result_message(result: ToolExecutionResult) -> ToolResultMessage:
    if result.content is not None:
        content = tuple(
            TextContent(item.text)
            if isinstance(item, ToolTextContent)
            else JsonContent(item.value)
            if isinstance(item, ToolJsonContent)
            else item
            for item in result.content
        )
        if not all(
            isinstance(item, (TextContent, JsonContent, ArtifactReferenceContent))
            for item in content
        ):
            raise TypeError("Unknown canonical ToolContent.")
        return ToolResultMessage(
            result.call_id, result.name, ToolResultStatus.SUCCESS, content
        )
    assert result.error is not None
    return ToolResultMessage(
        result.call_id,
        result.name,
        ToolResultStatus.ERROR,
        (TextContent(result.error.message),),
        result.error,
    )


def committed_effects(
    effects: tuple[ToolEffectRecord, ...],
) -> tuple[ToolEffectRecord, ...]:
    return tuple(replace(effect, transcript_committed=True) for effect in effects)


def retry_safe(
    effects: tuple[ToolEffectRecord, ...], retry_blockers: tuple[object, ...] = ()
) -> bool:
    if retry_blockers:
        return False
    for effect in effects:
        if effect.effect_kind is ToolEffectKind.READ_ONLY:
            continue
        if effect.status is ToolEffectStatus.SUCCEEDED:
            if (
                effect.certainty is not EffectCertainty.CERTAIN
                or not effect.transcript_committed
            ):
                return False
        elif effect.status in {
            ToolEffectStatus.FAILED,
            ToolEffectStatus.CANCELLED,
            ToolEffectStatus.TIMED_OUT,
        }:
            if effect.certainty is not EffectCertainty.CERTAIN_NO_EFFECT:
                return False
        else:
            return False
    return True


def _truncate_utf8(text: str, limit: int) -> str:
    marker = "\n...[truncated]"
    marker_bytes = marker.encode("utf-8")
    if limit <= len(marker_bytes):
        return marker_bytes[:limit].decode("utf-8", errors="ignore")
    payload = text.encode("utf-8")[: limit - len(marker_bytes)]
    return payload.decode("utf-8", errors="ignore") + marker


def _consume_task_result(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _flatten_effects(
    effects: list[tuple[ToolEffectRecord, ...] | None],
) -> tuple[ToolEffectRecord, ...]:
    return tuple(effect for group in effects if group is not None for effect in group)
