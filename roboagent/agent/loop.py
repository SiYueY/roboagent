"""Canonical V1 model/tool turn orchestration."""

from __future__ import annotations
import asyncio
import json
from typing import TYPE_CHECKING, Callable
from roboagent.agent.executor import (
    DefaultToolExecutionPolicy,
    ToolExecutor,
    tool_result,
)
from roboagent.agent.types import RunConfig
from roboagent.model.client import ChatModel
from roboagent.message import (
    AssistantMessage,
    ProtocolError,
    TextContent,
    ToolCall,
)
from roboagent.runtime.types import (
    ContentCompleted,
    ContentSummary,
    ModelCapabilities,
    ModelCompleted,
    ModelContext,
    ContextPreparationError,
    MediaResolutionError,
    ModelCapabilityError,
    ModelFailed,
    ModelProtocolError,
    ModelRequest,
    Modality,
    RunContext,
    RunPhase,
    TextDelta,
    ToolCallDelta,
    ToolCallSummary,
    content_summary,
    modality,
)

if TYPE_CHECKING:
    from roboagent.agent.agent import Agent
    from roboagent.agent.session import AgentSession


class MaxTurnsError(Exception):
    def __init__(self, turns: int) -> None:
        self.turns = turns
        super().__init__("max_turns")


def _validate_input(capabilities: ModelCapabilities, context: ModelContext) -> None:
    for message in context.messages:
        allowed = (
            capabilities.tool_result_modalities
            if message.role == "tool"
            else capabilities.input_modalities
        )
        if any(modality(item) not in allowed for item in message.content):
            raise ModelCapabilityError("unsupported_input_modality")
    if context.tools and not capabilities.supports_tools:
        raise ModelCapabilityError("model_does_not_support_tools")


async def collect_model(
    model: ChatModel,
    request: ModelRequest,
    emit: Callable[..., object],
    update_state: Callable[..., object] | None = None,
) -> AssistantMessage:
    text = ""
    content: list[object] = []
    calls: dict[int, dict[str, str]] = {}
    async for item in model.stream(request, request.run_context.cancellation):
        if isinstance(item, TextDelta):
            text += item.text
            if update_state:
                update_state(
                    RunPhase.MODEL,
                    request.run_context.turn,
                    streaming_content=tuple(content_summary(value) for value in content)
                    + (ContentSummary(Modality.TEXT, size=len(text)),),
                )
            await emit("model_delta", text=item.text, content=())
        elif isinstance(item, ContentCompleted):
            if isinstance(item.content, TextContent):
                raise ValueError("ContentCompleted cannot contain TextContent")
            if text:
                content.append(TextContent(text))
                text = ""
            content.append(item.content)
            if update_state:
                update_state(
                    RunPhase.MODEL,
                    request.run_context.turn,
                    streaming_content=tuple(
                        content_summary(value) for value in content
                    ),
                )
            await emit("model_delta", content=(content_summary(item.content),))
        elif isinstance(item, ToolCallDelta):
            part = calls.setdefault(item.index, {"id": "", "name": "", "arguments": ""})
            if item.call_id and part["id"] and part["id"] != item.call_id:
                raise ModelProtocolError("conflicting_tool_call_id")
            if item.name and part["name"] and part["name"] != item.name:
                raise ModelProtocolError("conflicting_tool_call_name")
            part["id"] = item.call_id or part["id"]
            part["name"] = item.name or part["name"]
            part["arguments"] += item.arguments_delta
        elif isinstance(item, ModelCompleted):
            message = item.message
            if not isinstance(message, AssistantMessage):
                raise ModelProtocolError("invalid_completed_message")
            if text:
                content.append(TextContent(text))
            if content:
                message = AssistantMessage(
                    tuple(content),
                    message.tool_calls,
                    message.finish_reason,
                    message.model,
                    message.timestamp,
                    usage=message.usage,
                )
            if calls:
                formed = []
                for index in sorted(calls):
                    part = calls[index]
                    if not part["id"] or not part["name"]:
                        raise ModelProtocolError("incomplete_tool_call")
                    try:
                        args = json.loads(part["arguments"])
                    except json.JSONDecodeError as exc:
                        raise ModelProtocolError("invalid_tool_arguments") from exc
                    if not isinstance(args, dict):
                        raise ModelProtocolError("invalid_tool_arguments")
                    try:
                        formed.append(
                            ToolCall(part["id"], part["name"], part["arguments"], args)
                        )
                    except ProtocolError as exc:
                        raise ModelProtocolError("invalid_tool_call") from exc
                try:
                    message = AssistantMessage(
                        message.content,
                        tuple(formed),
                        message.finish_reason,
                        message.model,
                        message.timestamp,
                        usage=message.usage,
                    )
                except ProtocolError as exc:
                    raise ModelProtocolError("duplicate_tool_call_id") from exc
            return message
        elif isinstance(item, ModelFailed):
            raise ModelProtocolError(item.error or "model_stream_failed")
        else:
            raise ModelProtocolError("unknown_model_stream_item")
    raise ModelProtocolError("model_stream_ended_without_completion")


async def run_loop(
    *,
    agent: Agent,
    session: AgentSession,
    run_context: RunContext,
    config: RunConfig,
    emit: Callable[..., object],
    consume_controls: Callable[[], tuple[object, ...]],
    observe_controls: Callable[[], tuple[object, ...]],
    wait_for_control: Callable[[int], object],
    update_state: Callable[..., object],
    hook: Callable[..., object],
) -> tuple[AssistantMessage | None, int]:
    final: AssistantMessage | None = None
    caps: ModelCapabilities = agent.model.capabilities
    for turn in range(1, config.max_turns + 1):
        if run_context.cancellation.cancelled:
            raise asyncio.CancelledError()
        # This is the only control-consumption boundary: no model stream or tool
        # batch is in flight, so appending preserves transcript grammar.
        for control in consume_controls():
            session._append(control.message)
        run_context = RunContext(
            run_context.session_id,
            run_context.run_id,
            run_context.cancellation,
            turn,
            run_context.metadata,
        )
        await emit("turn_started", turn=turn)
        await hook("on_turn_start", run_context)
        update_state(RunPhase.PREPARING_CONTEXT, turn)
        try:
            frozen = await agent.tool_resolver.resolve(run_context, agent.tools)
            tools = frozen.by_name()
            context = await agent.context_manager.prepare(
                system_prompt=agent.system_prompt,
                messages=session.messages,
                tools=frozen.definitions,
                cancellation=run_context.cancellation,
            )
            if not isinstance(context, ModelContext):
                raise TypeError("ContextManager must return ModelContext.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ContextPreparationError() from exc
        try:
            _validate_input(caps, context)
        except ModelCapabilityError as exc:
            await emit(
                "model_failed",
                turn=turn,
                error_code=exc.code,
                error="Model cannot accept this canonical context.",
            )
            raise
        update_state(RunPhase.MODEL, turn)
        await emit("model_started", turn=turn)
        await hook("on_model_start", context)
        try:
            message = await collect_model(
                agent.model,
                ModelRequest(
                    agent.model.model_name, context, run_context, agent.media_resolver
                ),
                emit,
                update_state,
            )
        except asyncio.CancelledError:
            raise
        except ModelProtocolError as exc:
            await emit(
                "model_failed",
                turn=turn,
                error_code=exc.code,
                error="Model stream could not be normalized.",
            )
            raise
        except MediaResolutionError as exc:
            await emit(
                "model_failed",
                turn=turn,
                error_code=exc.code.value,
                error="Model media preparation failed.",
            )
            raise
        except Exception:
            await emit(
                "model_failed",
                turn=turn,
                error_code="model_error",
                error="Model invocation failed.",
            )
            raise
        if run_context.cancellation.cancelled:
            raise asyncio.CancelledError()
        if any(
            modality(part) not in caps.output_modalities for part in message.content
        ):
            error = ModelCapabilityError("unsupported_output_modality")
            await emit(
                "model_failed",
                turn=turn,
                error_code=error.code,
                error="Model emitted unsupported canonical content.",
            )
            raise error
        session._append(message)
        final = message
        await emit(
            "model_completed",
            turn=turn,
            content=tuple(content_summary(part) for part in message.content),
        )
        await hook("on_model_end", message)
        if not message.tool_calls:
            update_state(RunPhase.BETWEEN_TURNS, turn)
            await emit("turn_completed", turn=turn)
            await hook("on_turn_end", run_context)
            controls = consume_controls()
            if controls:
                # A completed assistant response is a safe boundary.  Controls
                # received while streaming become the next model input in strict
                # receive order rather than being lost to natural completion.
                for control in controls:
                    session._append(control.message)
                continue
            return final, turn
        policy = (
            config.tool_policy_factory(run_context)
            if config.tool_policy_factory
            else DefaultToolExecutionPolicy()
        )
        executor = ToolExecutor(
            tools,
            session._media_limits,
            config.tool_execution,
            policy,
            emit=emit,
            hook=hook,
            observe_controls=observe_controls,
            wait_for_control=wait_for_control,
        )
        update_state(
            RunPhase.TOOL,
            turn,
            pending_tool_calls=tuple(
                ToolCallSummary(call.id, call.name) for call in message.tool_calls
            ),
        )
        await emit("tool_batch_started", turn=turn)
        batch = await executor.execute(message.tool_calls, run_context, context)
        for outcome in batch.outcomes:
            result = tool_result(outcome, limits=session._media_limits)
            session._append(result)
        update_state(RunPhase.BETWEEN_TURNS, turn)
        await emit("turn_completed", turn=turn)
        await hook("on_turn_end", run_context)
        if batch.fail_run:
            raise RuntimeError("tool_policy_fail_run")
    raise MaxTurnsError(config.max_turns)
