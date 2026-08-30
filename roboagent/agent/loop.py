"""Low-level sequential agent loop; it owns no persistent agent state."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from roboagent.agent.types import AfterToolCall, BeforeToolCall, ContextTransform, ToolCallDecision
from roboagent.model.client import ChatModel
from roboagent.runtime import (
    AgentEvent, AssistantMessage, CancellationToken, Message, MessageEvent, ModelContext, ModelRequest,
    RuntimeErrorEvent, ToolCall, ToolEvent, ToolResultMessage, TurnEvent,
)
from roboagent.tool import Tool, ToolInvocation

EventSink = Callable[[AgentEvent], Awaitable[None]]


async def run_loop(*, model: ChatModel, system_prompt: str | None, messages: list[Message], tools: Sequence[Tool],
                   cancellation: CancellationToken, emit: EventSink, run_id: str, max_turns: int,
                   transforms: Sequence[ContextTransform] = (), before_tool_call: BeforeToolCall | None = None,
                   after_tool_call: AfterToolCall | None = None) -> tuple[AssistantMessage | None, str, str | None]:
    final: AssistantMessage | None = None
    for turn in range(1, max_turns + 1):
        if cancellation.cancelled:
            return final, "cancelled", "Run cancelled."
        await emit(TurnEvent(turn, "start"))
        context = ModelContext(system_prompt, tuple(messages), tuple(tool.definition for tool in tools))
        try:
            for transform in transforms:
                context = await _await(transform(context))
                if not isinstance(context, ModelContext):
                    raise TypeError("Context transforms must return ModelContext.")
        except Exception as exc:
            await emit(RuntimeErrorEvent(f"Context transform failed: {exc}", turn))
            return final, "failed", f"Context transform failed: {exc}"
        assistant, failure = await _stream_message(model, ModelRequest(model.model_name, context), cancellation, emit)
        if failure:
            await emit(RuntimeErrorEvent(failure, turn))
            return final, "cancelled" if cancellation.cancelled else "failed", failure
        assert assistant is not None
        final = assistant
        messages.append(assistant)
        await emit(MessageEvent(assistant, phase="end"))
        results: list[bool] = []
        for call in assistant.tool_calls:
            await emit(ToolEvent(call, "start"))
            result, terminate = await _execute_tool(call, assistant.finish_reason, tools, context, cancellation, run_id, turn, before_tool_call, after_tool_call)
            messages.append(result)
            await emit(ToolEvent(call, "end", result))
            results.append(terminate)
            if cancellation.cancelled:
                return final, "cancelled", "Run cancelled."
        await emit(TurnEvent(turn, "end"))
        if not assistant.tool_calls:
            return final, "completed", None
        if results and all(results):
            return final, "completed", None
    error = f"Agent exceeded max_turns={max_turns}."
    await emit(RuntimeErrorEvent(error, max_turns))
    return final, "max_turns", error


async def _stream_message(model: ChatModel, request: ModelRequest, cancellation: CancellationToken, emit: EventSink) -> tuple[AssistantMessage | None, str | None]:
    started, partial = False, ""
    async for event in model.stream(request, cancellation):
        if event.type == "start":
            started = True
            await emit(MessageEvent(AssistantMessage(model=model.model_name), phase="start"))
        elif event.type in {"text_delta", "tool_call_delta"}:
            partial += event.delta if event.type == "text_delta" else ""
            await emit(MessageEvent(delta=event.delta, phase="delta"))
        elif event.type == "done":
            if event.message is None:
                return None, "Model completed without an assistant message."
            return replace(event.message, content=partial) if event.message.content != partial else event.message, None
        elif event.type in {"error", "cancelled"}:
            return None, event.error or "Model request failed."
    return None, "Model stream ended without a terminal event." if started else "Model stream produced no events."


async def _execute_tool(call: ToolCall, finish_reason: str, tools: Sequence[Tool], context: ModelContext, cancellation: CancellationToken,
                        run_id: str, turn: int, before: BeforeToolCall | None, after: AfterToolCall | None) -> tuple[ToolResultMessage, bool]:
    def error(text: str, terminate: bool = False) -> tuple[ToolResultMessage, bool]:
        return ToolResultMessage(call.id, call.name, text, True), terminate
    if finish_reason == "length": return error("Tool call was not executed because the model response was truncated.")
    tool = next((item for item in tools if item.name == call.name), None)
    if tool is None: return error(f"Unknown tool '{call.name}'.")
    if call.parse_error or call.arguments is None: return error(f"Tool '{call.name}' received invalid JSON arguments: {call.parse_error or 'missing object'}")
    params = tool.validate(dict(call.arguments))
    if isinstance(params, str): return error(f"Tool '{call.name}' received invalid arguments: {params}")
    invocation = ToolInvocation(run_id, turn, call, context, cancellation)
    try:
        decision = await _await(before(invocation)) if before else ToolCallDecision()
        decision = decision or ToolCallDecision()
    except Exception as exc:
        return error(f"before_tool_call hook failed: {exc}")
    if not decision.allow: return error(decision.reason or f"Tool '{call.name}' was blocked by policy.", decision.terminate)
    execution = await tool.execute(params, invocation)
    try:
        override = await _await(after(invocation, execution)) if after else None
    except Exception as exc:
        return error(f"after_tool_call hook failed: {exc}")
    if override:
        execution = replace(execution, content=override.content if override.content is not None else execution.content,
                            details=execution.details if override.details is None else override.details,
                            is_error=execution.is_error if override.is_error is None else override.is_error,
                            terminate=execution.terminate if override.terminate is None else override.terminate)
    return ToolResultMessage(call.id, call.name, execution.content, execution.is_error, execution.details), execution.terminate


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value
