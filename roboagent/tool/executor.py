"""Canonical policy-aware ToolExecutor."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, replace
from typing import Protocol, Sequence
from uuid import uuid4

from roboagent.message import ArtifactReferenceContent, JsonContent, TextContent, ToolCall, ToolResultMessage, ToolResultStatus, canonical_json_digest
from roboagent.runtime.types import RunError

from .tool import (
    AllowAllToolPolicy,
    RawToolResult,
    Tool,
    ToolBatchAborted,
    ToolBatchResult,
    ToolContent,
    ToolContractError,
    ToolContext,
    ToolDecision,
    ToolEffectKind,
    ToolEffectRecord,
    ToolEffectStatus,
    ToolEffectUnknown,
    ToolErrorInfo,
    ToolExecutionMode,
    ToolExecutionFailure,
    ToolExecutionPolicy,
    ToolExecutionResult,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
    validate_tool_arguments,
)
from .approval import ApprovalDecision, ApprovalProvider, ApprovalRequest, ApprovalResponse, ApprovalSettings
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
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (self.max_calls_per_turn, self.max_concurrency)):
            raise ValueError("Tool call and concurrency limits must be positive.")
        if self.default_timeout is not None and (isinstance(self.default_timeout, bool) or not isinstance(self.default_timeout, (int, float)) or self.default_timeout <= 0):
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
    async def emit(self, event_type: str, **payload: object) -> object: ...


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

    async def execute(self, calls: tuple[ToolCall, ...], context: ToolContext) -> ToolBatchResult:
        if len(calls) > self.config.max_calls_per_turn:
            raise ToolBatchAborted(RunError("max_calls_per_turn", "Tool call limit exceeded."))
        context.cancellation.raise_if_cancelled()
        tools = tuple(self.registry.get(call.name) for call in calls)
        concurrent = bool(calls) and all(
            tool is not None and tool.execution_mode is ToolExecutionMode.CONCURRENT
            for tool in tools
        )
        if not concurrent:
            results: list[ToolExecutionResult] = []
            effects: list[ToolEffectRecord] = []
            for call in calls:
                try:
                    result, effect = await self._one(call, context)
                except ToolBatchAborted as exc:
                    raise ToolBatchAborted(exc.reason, (*effects, *exc.effects)) from exc
                except ToolBatchCancelled as exc:
                    raise ToolBatchCancelled((*effects, *exc.effects)) from exc
                except asyncio.CancelledError as exc:
                    raise ToolBatchCancelled(tuple(effects)) from exc
                results.append(result)
                if effect:
                    effects.append(effect)
            return ToolBatchResult(calls, tuple(results), tuple(effects))
        return await self._concurrent(calls, context)

    async def _concurrent(self, calls: tuple[ToolCall, ...], context: ToolContext) -> ToolBatchResult:
        results: list[ToolExecutionResult | None] = [None] * len(calls)
        effects: list[ToolEffectRecord | None] = [None] * len(calls)
        prepared: list[tuple[int, _PreparedCall]] = []
        for index, call in enumerate(calls):
            try:
                value = await self._prepare(call, context)
            except asyncio.CancelledError as exc:
                raise ToolBatchCancelled(()) from exc
            if isinstance(value, ToolExecutionResult):
                results[index] = value
            else:
                prepared.append((index, value))
        running: dict[asyncio.Task[tuple[ToolExecutionResult, ToolEffectRecord | None]], int] = {}
        next_index = 0

        def fill() -> None:
            nonlocal next_index
            while next_index < len(prepared) and len(running) < self.config.max_concurrency:
                index, item = prepared[next_index]
                task = asyncio.create_task(self._execute_prepared(item, context))
                running[task] = index
                next_index += 1

        fill()
        try:
            while running:
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    index = running.pop(task)
                    try:
                        result, effect = task.result()
                    except ToolBatchAborted as exc:
                        cancelled_effects = await self._cancel_tasks(running)
                        raise ToolBatchAborted(
                            exc.reason,
                            (*_present_effects(effects), *exc.effects, *cancelled_effects),
                        ) from exc
                    except ToolBatchCancelled as exc:
                        cancelled_effects = await self._cancel_tasks(running)
                        raise ToolBatchCancelled(
                            (*_present_effects(effects), *exc.effects, *cancelled_effects)
                        ) from exc
                    results[index] = result
                    if effect:
                        effects[index] = effect
                fill()
        except asyncio.CancelledError:
            cancelled_effects = await self._cancel_tasks(running)
            raise ToolBatchCancelled((*_present_effects(effects), *cancelled_effects))
        return ToolBatchResult(
            calls,
            tuple(item for item in results if item is not None),
            _present_effects(effects),
        )

    async def _cancel_tasks(self, running: dict[asyncio.Task[object], int]) -> tuple[ToolEffectRecord, ...]:
        for task in running:
            task.cancel()
        settled = await asyncio.gather(*running, return_exceptions=True)
        effects: list[ToolEffectRecord] = []
        for value in settled:
            if isinstance(value, ToolBatchCancelled):
                effects.extend(value.effects)
            elif isinstance(value, ToolBatchAborted):
                effects.extend(value.effects)
            elif isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], ToolEffectRecord):
                effects.append(value[1])
        running.clear()
        return tuple(effects)

    async def _one(self, call: ToolCall, context: ToolContext) -> tuple[ToolExecutionResult, ToolEffectRecord | None]:
        prepared = await self._prepare(call, context)
        if isinstance(prepared, ToolExecutionResult):
            return prepared, None
        return await self._execute_prepared(prepared, context)

    async def _prepare(self, call: ToolCall, context: ToolContext) -> _PreparedCall | ToolExecutionResult:
        context.cancellation.raise_if_cancelled()
        tool = self.registry.get(call.name)
        try:
            decision = await self.policy.evaluate(call, tool, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolBatchAborted(RunError("policy_error", "Tool policy evaluation failed.", cause_type=type(exc).__name__)) from exc
        if isinstance(decision, ToolDecision):
            decision = ToolPolicyDecision(decision)
        if not isinstance(decision, ToolPolicyDecision):
            raise ToolBatchAborted(RunError("policy_error", "Tool policy returned an invalid decision."))
        if decision.action is ToolDecision.FAIL_RUN:
            await self._emit_terminal("tool.failed", call, "policy_fail_run")
            raise ToolBatchAborted(RunError("policy_fail_run", "Tool policy failed the Run."))
        if decision.action is ToolDecision.REJECT:
            result = self._error(call, "rejected", "Tool call rejected by policy.")
            await self._emit_terminal("tool.failed", call, "rejected")
            await self._after_tool(context, result)
            return result
        if tool is None:
            result = self._error(call, "unknown_tool", "Unknown tool.")
            await self._emit_terminal("tool.failed", call, "unknown_tool")
            await self._after_tool(context, result)
            return result
        if error := validate_tool_arguments(tool, call.arguments):
            result = ToolExecutionResult(call.id, call.name, error=self._bounded_error(error))
            await self._emit_terminal("tool.failed", call, "invalid_arguments")
            await self._after_tool(context, result)
            return result
        if decision.action is ToolDecision.REQUIRE_APPROVAL:
            approval = await self._approval(call, context, decision.reason)
            if approval is not None:
                await self._after_tool(context, approval)
                return approval
        try:
            timeout_resolver = getattr(tool, "requested_timeout", None)
            timeout = timeout_resolver(call.arguments) if timeout_resolver is not None else None
        except Exception as exc:
            raise ToolBatchAborted(
                RunError("tool_contract_error", "Tool timeout resolution failed.", cause_type=type(exc).__name__)
            ) from exc
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0
        ):
            raise ToolBatchAborted(RunError("tool_contract_error", "Tool timeout resolution was invalid."))
        timeout = timeout if timeout is not None else tool.timeout if tool.timeout is not None else self.config.default_timeout
        return _PreparedCall(call, tool, timeout)

    async def _execute_prepared(
        self,
        prepared: _PreparedCall,
        context: ToolContext,
    ) -> tuple[ToolExecutionResult, ToolEffectRecord | None]:
        call, tool, timeout = prepared.call, prepared.tool, prepared.timeout
        try:
            await self._before_tool(context, call)
        except ToolBatchAborted:
            await self._emit_terminal("tool.failed", call, "hook_error")
            raise
        context.cancellation.raise_if_cancelled()
        await self._emit("tool.started", call)
        task = asyncio.create_task(tool.execute(call.arguments, context))
        cancelled = asyncio.create_task(context.cancellation.wait_cancelled())
        try:
            waiters: set[asyncio.Task[object]] = {task, cancelled}  # type: ignore[arg-type]
            done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                effect = await self._cancel_started(task, tool, call)
                await self._emit("tool.cancelled", call, status="cancelled", error_code="cancelled")
                raise ToolBatchCancelled((effect,))
            if task not in done:
                effect = await self._cancel_started(task, tool, call, timed_out=True)
                result = ToolExecutionResult(
                    call.id,
                    call.name,
                    error=ToolErrorInfo("timeout", "Tool execution timed out."),
                )
                await self._emit_terminal("tool.failed", call, "timeout")
                await self._after_with_effect(context, result, effect)
                return result, effect
            returned = task.result()
            raw = returned if isinstance(returned, RawToolResult) else RawToolResult((returned,))
            evidence = raw_result_evidence(raw)
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.SUCCEEDED, content=evidence)
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
                await self._emit_terminal("tool.failed", call, exc.code)
                raise ToolBatchAborted(
                    RunError(exc.code, "Tool result materialization failed.", cause_type=type(exc).__name__),
                    (effect,),
                ) from exc
            except Exception as exc:
                await self._emit_terminal("tool.failed", call, "tool_materialization_error")
                raise ToolBatchAborted(
                    RunError("tool_materialization_error", "Tool result materialization failed.", cause_type=type(exc).__name__),
                    (effect,),
                ) from exc
            result = ToolExecutionResult(call.id, call.name, content=content)
            await self._emit_terminal("tool.completed", call, None)
            await self._after_with_effect(context, result, effect)
            return result, effect
        except ToolBatchCancelled:
            raise
        except ToolBatchAborted:
            raise
        except ToolEffectUnknown as exc:
            error = self._bounded_error(exc.error)
            result = ToolExecutionResult(call.id, call.name, error=error)
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.UNKNOWN, error=error)
            await self._emit_terminal("tool.failed", call, error.code)
            await self._after_with_effect(context, result, effect)
            return result, effect
        except ToolExecutionFailure as exc:
            error = self._bounded_error(exc.error)
            result = ToolExecutionResult(call.id, call.name, error=error)
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.FAILED, error=error)
            await self._emit_terminal("tool.failed", call, error.code)
            await self._after_with_effect(context, result, effect)
            return result, effect
        except ToolContractError as exc:
            error = self._bounded_error(ToolErrorInfo("invalid_tool_output", "Tool returned non-canonical output."))
            status = (
                ToolEffectStatus.UNKNOWN
                if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING
                else ToolEffectStatus.FAILED
            )
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, status, error=error)
            await self._emit_terminal("tool.failed", call, error.code)
            raise ToolBatchAborted(
                RunError("tool_contract_error", "Tool violated its output contract.", cause_type=type(exc).__name__),
                (effect,),
            ) from exc
        except asyncio.CancelledError:
            effect = await self._cancel_started(task, tool, call)
            await self._emit("tool.cancelled", call, status="cancelled", error_code="cancelled")
            raise ToolBatchCancelled((effect,))
        except Exception:
            error = self._bounded_error(ToolErrorInfo("execution_error", "Tool execution failed."))
            result = ToolExecutionResult(call.id, call.name, error=error)
            if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
                effect_error = self._bounded_error(
                    ToolErrorInfo("effect_unknown", "Tool side effect could not be determined.")
                )
                effect = ToolEffectRecord(
                    call.id,
                    call.name,
                    tool.effect_kind,
                    ToolEffectStatus.UNKNOWN,
                    error=effect_error,
                )
            else:
                effect = ToolEffectRecord(
                    call.id,
                    call.name,
                    tool.effect_kind,
                    ToolEffectStatus.FAILED,
                    error=error,
                )
            await self._emit_terminal("tool.failed", call, "execution_error")
            await self._after_with_effect(context, result, effect)
            return result, effect
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _cancel_started(
        self,
        task: asyncio.Task[ToolContent | RawToolResult],
        tool: Tool,
        call: ToolCall,
        *,
        timed_out: bool = False,
    ) -> ToolEffectRecord:
        task.cancel()
        try:
            content = await asyncio.wait_for(asyncio.shield(task), self.config.cancellation_grace_period)
        except TimeoutError:
            task.cancel()
            task.add_done_callback(_consume_task_result)
        except ToolEffectUnknown as exc:
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.UNKNOWN, error=self._bounded_error(exc.error))
        except ToolExecutionFailure as exc:
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.FAILED, error=self._bounded_error(exc.error))
        except asyncio.CancelledError:
            content = None
        except Exception:
            if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
                error = ToolErrorInfo("effect_unknown", "Tool side effect could not be determined.")
                return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.UNKNOWN, error=error)
            error = ToolErrorInfo("execution_error", "Tool execution failed during cancellation.")
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.FAILED, error=error)
        else:
            raw = content if isinstance(content, RawToolResult) else RawToolResult((content,))
            evidence = raw_result_evidence(raw)
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.SUCCEEDED, content=evidence)
        if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
            error = ToolErrorInfo(
                "timeout" if timed_out else "effect_unknown",
                "Tool execution timed out; its side effect could not be determined."
                if timed_out
                else "Tool side effect could not be determined.",
            )
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.UNKNOWN, error=error)
        if timed_out:
            error = ToolErrorInfo("timeout", "Tool execution timed out.")
            status = ToolEffectStatus.TIMED_OUT
        else:
            error = ToolErrorInfo("cancelled", "Tool execution was cancelled.")
            status = ToolEffectStatus.CANCELLED
        return ToolEffectRecord(call.id, call.name, tool.effect_kind, status, error=error)

    async def _approval(
        self,
        call: ToolCall,
        context: ToolContext,
        reason: str | None,
    ) -> ToolExecutionResult | None:
        if self.approval_provider is None:
            raise ToolBatchAborted(RunError("approval_error", "Tool approval is required but no provider is configured."))
        request = ApprovalRequest(
            uuid4().hex,
            context.run_id,
            context.session_id,
            call.id,
            call.name,
            call.arguments,
            canonical_json_digest(call.arguments),
            reason,
        )
        task = asyncio.create_task(self.approval_provider.request(request, context.cancellation))
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
                raise asyncio.CancelledError()
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                context.cancellation.raise_if_cancelled()
                result = self._error(call, "approval_timeout", "Tool approval timed out.")
                await self._emit_terminal("tool.failed", call, "approval_timeout")
                return result
            try:
                response = task.result()
            except asyncio.CancelledError:
                context.cancellation.raise_if_cancelled()
                raise ToolBatchAborted(RunError("approval_error", "Approval provider cancelled unexpectedly."))
            except Exception as exc:
                raise ToolBatchAborted(
                    RunError("approval_error", "Approval provider failed.", cause_type=type(exc).__name__)
                ) from exc
            if not isinstance(response, ApprovalResponse):
                raise ToolBatchAborted(RunError("approval_error", "Approval provider returned an invalid response."))
            if response.approval_id != request.approval_id or response.arguments_digest != request.arguments_digest:
                raise ToolBatchAborted(RunError("approval_mismatch", "Approval response did not match the ToolCall."))
            if response.decision is ApprovalDecision.REJECT:
                result = self._error(call, "approval_rejected", "Tool call was not approved.")
                await self._emit_terminal("tool.failed", call, "approval_rejected")
                return result
            return None
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _before_tool(self, context: ToolContext, call: ToolCall) -> None:
        from roboagent.agent.hooks import HookDecision, ToolHookContext
        from roboagent.runtime.types import RunContext

        hook_context = ToolHookContext(RunContext(context.run_id, context.session_id, context.cancellation))
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
                raise ToolBatchAborted(RunError("hook_error", "before_tool hook failed.", cause_type=type(exc).__name__)) from exc
            if not isinstance(decision, HookDecision):
                raise ToolBatchAborted(RunError("hook_error", "before_tool hook returned an invalid decision."))
            if getattr(decision, "value", decision) == "fail_run":
                raise ToolBatchAborted(RunError("hook_error", "before_tool hook failed the Run."))

    async def _after_tool(self, context: ToolContext, result: ToolExecutionResult) -> None:
        from roboagent.agent.hooks import ToolHookContext
        from roboagent.runtime.types import RunContext

        context.cancellation.raise_if_cancelled()
        hook_context = ToolHookContext(RunContext(context.run_id, context.session_id, context.cancellation))
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
                raise ToolBatchAborted(RunError("hook_error", "after_tool hook failed.", cause_type=type(exc).__name__)) from exc

    async def _after_with_effect(
        self,
        context: ToolContext,
        result: ToolExecutionResult,
        effect: ToolEffectRecord,
    ) -> None:
        try:
            await self._after_tool(context, result)
        except asyncio.CancelledError as exc:
            raise ToolBatchCancelled((effect,)) from exc
        except ToolBatchAborted as exc:
            raise ToolBatchAborted(exc.reason, (effect,)) from exc

    async def _await_hook(self, awaitable: object, context: ToolContext) -> object:
        task = asyncio.ensure_future(awaitable)  # type: ignore[arg-type]
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
        return ToolExecutionResult(call.id, call.name, error=self._bounded_error(ToolErrorInfo(code, message)))

    def _bounded_error(self, error: ToolErrorInfo) -> ToolErrorInfo:
        if len(error.message) <= self.config.max_error_chars:
            return error
        limit = self.config.max_error_chars
        message = "…" if limit == 1 else error.message[: limit - 1] + "…"
        return ToolErrorInfo(error.code, message, error.retryable)

    async def _emit(self, event_type: str, call: ToolCall, **payload: object) -> None:
        if self.events:
            await self.events.emit(event_type, tool_call_id=call.id, tool_name=call.name, **payload)

    async def _emit_terminal(self, event_type: str, call: ToolCall, error_code: str | None) -> None:
        await self._emit(event_type, call, error_code=error_code)


def result_message(result: ToolExecutionResult) -> ToolResultMessage:
    if result.content is not None:
        content = tuple(
            TextContent(item.text)
            if isinstance(item, ToolTextContent)
            else JsonContent(item.value)
            if hasattr(item, "value")
            else item
            for item in result.content
        )
        if not all(isinstance(item, (TextContent, JsonContent, ArtifactReferenceContent)) for item in content):
            raise TypeError("Unknown canonical ToolContent.")
        return ToolResultMessage(result.call_id, result.name, ToolResultStatus.SUCCESS, content)
    assert result.error is not None
    return ToolResultMessage(
        result.call_id,
        result.name,
        ToolResultStatus.ERROR,
        (TextContent(result.error.message),),
        result.error,
    )


def committed_effects(effects: tuple[ToolEffectRecord, ...]) -> tuple[ToolEffectRecord, ...]:
    return tuple(replace(effect, transcript_committed=True) for effect in effects)


def retry_safe(effects: tuple[ToolEffectRecord, ...]) -> bool:
    return not any(
        effect.effect_kind is ToolEffectKind.SIDE_EFFECTING
        and not effect.transcript_committed
        and effect.status in {ToolEffectStatus.SUCCEEDED, ToolEffectStatus.UNKNOWN}
        for effect in effects
    )


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


def _present_effects(
    effects: list[ToolEffectRecord | None],
) -> tuple[ToolEffectRecord, ...]:
    return tuple(effect for effect in effects if effect is not None)
    RawToolResult,
