"""Canonical policy-aware ToolExecutor."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from roboagent.message import TextContent, ToolCall, ToolResultMessage, ToolResultStatus, canonical_json_dumps
from roboagent.runtime.types import RunError

from .tool import (
    AllowAllToolPolicy,
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
    ToolJsonContent,
    ToolRegistry,
    ToolTextContent,
    validate_tool_arguments,
)


@dataclass(frozen=True, slots=True)
class ToolExecutorConfig:
    max_calls_per_turn: int = 32
    max_concurrency: int = 8
    default_timeout: float | None = 60.0
    cancellation_grace_period: float = 2.0
    max_output_bytes: int = 64 * 1024
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
            or any(not isinstance(value, int) or isinstance(value, bool) for value in (self.max_output_bytes, self.max_error_chars))
            or self.max_output_bytes < 16
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
    ) -> None:
        self.registry = registry
        self.policy = policy or AllowAllToolPolicy()
        self.hooks = tuple(hooks)
        self.events = events
        self.config = config or ToolExecutorConfig()
        self.hook_timeout = hook_timeout

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
        effects: list[ToolEffectRecord] = []
        running: dict[asyncio.Task[tuple[ToolExecutionResult, ToolEffectRecord | None]], int] = {}
        next_index = 0

        def fill() -> None:
            nonlocal next_index
            while next_index < len(calls) and len(running) < self.config.max_concurrency:
                task = asyncio.create_task(self._one(calls[next_index], context))
                running[task] = next_index
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
                        raise ToolBatchAborted(exc.reason, (*effects, *exc.effects, *cancelled_effects)) from exc
                    except ToolBatchCancelled as exc:
                        cancelled_effects = await self._cancel_tasks(running)
                        raise ToolBatchCancelled((*effects, *exc.effects, *cancelled_effects)) from exc
                    results[index] = result
                    if effect:
                        effects.append(effect)
                fill()
        except asyncio.CancelledError:
            cancelled_effects = await self._cancel_tasks(running)
            raise ToolBatchCancelled((*effects, *cancelled_effects))
        return ToolBatchResult(calls, tuple(item for item in results if item is not None), tuple(effects))

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
        context.cancellation.raise_if_cancelled()
        tool = self.registry.get(call.name)
        try:
            decision = await self.policy.evaluate(call, tool, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolBatchAborted(RunError("policy_error", "Tool policy evaluation failed.", cause_type=type(exc).__name__)) from exc
        if not isinstance(decision, ToolDecision):
            raise ToolBatchAborted(RunError("policy_error", "Tool policy returned an invalid decision."))
        if decision is ToolDecision.FAIL_RUN:
            await self._emit_terminal("tool.failed", call, "policy_fail_run")
            raise ToolBatchAborted(RunError("policy_fail_run", "Tool policy failed the Run."))
        if decision is ToolDecision.REJECT:
            result = self._error(call, "rejected", "Tool call rejected by policy.")
            await self._emit_terminal("tool.failed", call, "rejected")
            await self._after_tool(context, result)
            return result, None
        if tool is None:
            result = self._error(call, "unknown_tool", "Unknown tool.")
            await self._emit_terminal("tool.failed", call, "unknown_tool")
            await self._after_tool(context, result)
            return result, None
        if error := validate_tool_arguments(tool, call.arguments):
            result = ToolExecutionResult(call.id, call.name, error=self._bounded_error(error))
            await self._emit_terminal("tool.failed", call, "invalid_arguments")
            await self._after_tool(context, result)
            return result, None
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
            content = self._bounded_content(task.result())
            result = ToolExecutionResult(call.id, call.name, content=content)
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.SUCCEEDED, content=content)
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
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.FAILED, error=error)
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
            effect = ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.FAILED, error=error)
            await self._emit_terminal("tool.failed", call, "execution_error")
            await self._after_with_effect(context, result, effect)
            return result, effect
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _cancel_started(
        self,
        task: asyncio.Task[ToolContent],
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
            bounded = self._bounded_content(content)
            return ToolEffectRecord(call.id, call.name, tool.effect_kind, ToolEffectStatus.SUCCEEDED, content=bounded)
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

    def _bounded_content(self, content: ToolContent) -> ToolContent:
        if isinstance(content, ToolJsonContent):
            serialized = canonical_json_dumps(content.value)
            if len(serialized.encode("utf-8")) <= self.config.max_output_bytes:
                return content
            return ToolTextContent(_truncate_utf8(serialized, self.config.max_output_bytes), True)
        encoded = content.text.encode("utf-8")
        if len(encoded) <= self.config.max_output_bytes:
            return content
        return ToolTextContent(_truncate_utf8(content.text, self.config.max_output_bytes), True)

    async def _emit(self, event_type: str, call: ToolCall, **payload: object) -> None:
        if self.events:
            await self.events.emit(event_type, tool_call_id=call.id, tool_name=call.name, **payload)

    async def _emit_terminal(self, event_type: str, call: ToolCall, error_code: str | None) -> None:
        await self._emit(event_type, call, error_code=error_code)


def result_message(result: ToolExecutionResult) -> ToolResultMessage:
    if result.content is not None:
        if isinstance(result.content, ToolTextContent):
            content = (TextContent(result.content.text),)
        else:
            content = (TextContent(canonical_json_dumps(result.content.value)),)
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
