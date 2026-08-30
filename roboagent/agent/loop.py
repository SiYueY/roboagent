"""The functional, sequential model-to-tool execution loop."""
from __future__ import annotations
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any
from roboagent.agent.types import AfterToolCall, BeforeToolCall, ContextTransform, ToolCallDecision
from roboagent.model.client import ChatModel
from roboagent.runtime import AssistantMessage, CancellationToken, Message, ModelContext, ModelRequest, ToolCall, ToolExecutionResult, ToolResultMessage
from roboagent.tool import Tool, ToolInvocation

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

async def run_loop(*, model: ChatModel, system_prompt: str | None, messages: list[Message],
                   tools: Mapping[str, Tool], definitions: tuple, cancellation: CancellationToken,
                   emit: Emit, run_id: str, max_turns: int, transforms: Sequence[ContextTransform] = (),
                   before_tool_call: BeforeToolCall | None = None,
                   after_tool_call: AfterToolCall | None = None) -> tuple[AssistantMessage | None, str, str | None]:
    final: AssistantMessage | None = None
    for turn in range(1, max_turns + 1):
        if cancellation.cancelled:
            return final, _cancel_status(cancellation), "Run cancelled."
        await emit("turn_started", {"turn": turn})
        context = ModelContext(system_prompt, tuple(messages), definitions)
        try:
            for transform in transforms:
                context = await _await(_call_transform(transform, context, cancellation))
                if not isinstance(context, ModelContext):
                    raise TypeError("Context transforms must return ModelContext.")
        except Exception:
            error = "Context transform failed."
            await emit("runtime_error", {"error": error, "turn": turn})
            return final, "failed", error
        assistant, failure = await _stream_message(model, ModelRequest(model.model_name, context), cancellation, emit, turn)
        if failure:
            status = _cancel_status(cancellation) if cancellation.cancelled else "failed"
            await emit("runtime_error", {"error": failure, "turn": turn})
            return final, status, failure
        assert assistant is not None
        final = assistant
        messages.append(assistant)
        await emit("message_completed", {"turn": turn, "message": assistant})
        if not assistant.tool_calls:
            await emit("turn_completed", {"turn": turn})
            return final, "completed", None
        stop_run = False
        for call in assistant.tool_calls:
            await emit("tool_started", {"turn": turn, "tool_call": call})
            result, stop_run = await _execute_tool(call, assistant.finish_reason, tools, context, cancellation, run_id, turn, before_tool_call, after_tool_call)
            messages.append(result)
            await emit("tool_completed", {"turn": turn, "tool_call": call, "result": result})
            if cancellation.cancelled:
                return final, _cancel_status(cancellation), "Run cancelled."
            if result.is_error or stop_run:
                break
        await emit("turn_completed", {"turn": turn})
        if stop_run:
            return final, "completed", None
    error = f"Agent exceeded max_turns={max_turns}."
    await emit("runtime_error", {"error": error, "turn": max_turns})
    return final, "max_turns", error

async def _stream_message(model: ChatModel, request: ModelRequest, cancellation: CancellationToken, emit: Emit, turn: int) -> tuple[AssistantMessage | None, str | None]:
    started = False
    text = ""
    async for event in model.stream(request, cancellation):
        if event.type == "start":
            started = True
            await emit("message_started", {"turn": turn, "message": AssistantMessage(model=model.model_name)})
        elif event.type == "text_delta":
            text += event.delta
            await emit("message_delta", {"turn": turn, "delta": event.delta, "kind": "text"})
        elif event.type == "tool_call_delta":
            await emit("message_delta", {"turn": turn, "delta": event.delta, "kind": "tool_call", "tool_call_index": event.tool_call_index})
        elif event.type == "done":
            if event.message is None:
                return None, "Model completed without an assistant message."
            message = event.message
            return (replace(message, content=text) if message.content != text else message), None
        elif event.type in {"error", "cancelled"}:
            return None, event.error or "Model request failed."
    return None, "Model stream ended without a terminal event." if started else "Model stream produced no events."

async def _execute_tool(call: ToolCall, finish_reason: str, tools: Mapping[str, Tool], context: ModelContext,
                        cancellation: CancellationToken, run_id: str, turn: int, before: BeforeToolCall | None,
                        after: AfterToolCall | None) -> tuple[ToolResultMessage, bool]:
    def fail(content: str, code: str, stop_run: bool = False) -> tuple[ToolResultMessage, bool]:
        return ToolResultMessage(call.id, call.name, content, True, error_code=code), stop_run
    if cancellation.cancelled:
        code = "timeout" if cancellation.reason == "timeout" else "cancelled"
        return fail("Tool execution was cancelled.", code)
    if finish_reason == "length":
        return fail("Tool call was not executed because the model response was truncated.", "invalid_arguments")
    tool = tools.get(call.name)
    if tool is None:
        return fail(f"Unknown tool '{call.name}'.", "unknown_tool")
    if call.parse_error or call.arguments is None:
        return fail("Tool arguments were not valid JSON.", "invalid_arguments")
    params = tool.validate(dict(call.arguments))
    if isinstance(params, str):
        return fail("Tool arguments did not match the required schema.", "invalid_arguments")
    invocation = ToolInvocation(run_id, turn, call, context, cancellation)
    try:
        decision = await _await(before(invocation)) if before else ToolCallDecision()
        decision = decision or ToolCallDecision()
    except Exception:
        return fail("Tool policy evaluation failed.", "backend_error")
    if not decision.allow:
        return fail(decision.reason or "Tool call was blocked by policy.", "policy_denied", decision.stop_run)
    execution = await tool.execute(params, invocation)
    try:
        override = await _await(after(invocation, execution)) if after else None
    except Exception:
        return fail("Tool result processing failed.", "backend_error")
    if override:
        execution = replace(execution, content=override.content if override.content is not None else execution.content,
                            details=execution.details if override.details is None else override.details,
                            is_error=execution.is_error if override.is_error is None else override.is_error,
                            error_code=execution.error_code if override.error_code is None else override.error_code,
                            stop_run=execution.stop_run if override.stop_run is None else override.stop_run)
    return ToolResultMessage(call.id, call.name, execution.content, execution.is_error, execution.details, execution.error_code), execution.stop_run

def _call_transform(transform: ContextTransform, context: ModelContext, cancellation: CancellationToken) -> Any:
    try:
        return transform(context, cancellation)
    except TypeError:
        return transform(context)  # compatibility for a straightforward one-argument callable

def _cancel_status(cancellation: CancellationToken) -> str:
    return "timed_out" if cancellation.reason == "timeout" else "cancelled"

async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value
