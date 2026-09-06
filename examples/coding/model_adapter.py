"""Buffered coding protocol adapter over an ordinary text model."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from roboagent.context import (
    ConservativeTokenEstimator,
    MessageSegment,
    ModelContext,
    ModelContextSegment,
    SummarySegment,
    WorkspaceReferenceSegment,
)
from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    FrozenJsonObject,
    JsonContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UserMessage,
    canonical_json_dumps,
    freeze_json,
    thaw_json,
)
from roboagent.model import (
    FinishReason,
    Model,
    ModelCapabilities,
    ModelEvent,
    ModelProtocolError,
    ModelResponse,
    ModelSettings,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageUpdated,
    collect_model_stream,
)
from roboagent.runtime import Modality
from roboagent.tool import ToolErrorInfo

from .protocol import (
    EXECUTE_PROTOCOL,
    CodingProtocolError,
    parse_python_fence,
    validate_final_value,
)

_ACTION_REMINDER = (
    "\n\nProtocol reminder: continue until the user's task is complete. For another action, "
    "reply with exactly one closed Python Markdown code fence. 'Python action:' above is "
    "transcript notation only; never emit that label."
)


@dataclass(slots=True)
class CodingRunState:
    run_id: str
    max_provider_calls: int
    provider_calls_used: int = 0

    def __post_init__(self) -> None:
        if (
            not self.run_id
            or type(self.max_provider_calls) is not int
            or self.max_provider_calls < 1
        ):
            raise ValueError("Invalid CodingRunState.")


class CodingModelAdapter:
    def __init__(self, provider: Model, *, max_protocol_retries: int = 1) -> None:
        if type(max_protocol_retries) is not int or max_protocol_retries < 0:
            raise ValueError("max_protocol_retries must be a non-negative integer.")
        self.provider = provider
        self.max_protocol_retries = max_protocol_retries
        self._state: CodingRunState | None = None
        self.worker_client: object | None = None
        self.capabilities = ModelCapabilities(
            input_modalities=provider.capabilities.input_modalities,
            output_modalities=frozenset({Modality.TEXT, Modality.FILE}),
            tool_calling=True,
            parallel_tool_calls=False,
            context_window=provider.capabilities.context_window,
        )

    def bind(self, state: CodingRunState) -> None:
        if self._state is not None:
            raise RuntimeError("CodingModelAdapter is already bound to a Run.")
        self._state = state

    def unbind(self, state: CodingRunState) -> None:
        if self._state is state:
            self._state = None

    async def stream(
        self, context: ModelContext, settings: ModelSettings | None = None
    ) -> AsyncIterator[ModelEvent]:
        state = self._state
        if state is None:
            raise ModelProtocolError(
                "coding_run_state_unavailable", "Coding adapter is not bound to a Run."
            )
        local = _local_final(context)
        if local is not None:
            async for event in _response_events(local, Usage(0, 0, 0)):
                yield event
            return
        projected = _project_context(context)
        window = self.provider.capabilities.context_window
        if (
            window is not None
            and ConservativeTokenEstimator().estimate(projected).input_tokens + 256
            > window
        ):
            raise ModelProtocolError(
                "coding_projection_budget_exceeded",
                "Coding provider projection exceeds the context budget reserve.",
            )
        reset_notice = bool(getattr(self.worker_client, "pending_reset_notice", False))
        if reset_notice:
            projected = _append_user(
                projected,
                "Interpreter state has been reset. Previously created Python variables are no longer available.",
            )
        total_usage: Usage | None = None
        last_error: ModelProtocolError | None = None
        for attempt in range(self.max_protocol_retries + 1):
            if state.provider_calls_used >= state.max_provider_calls:
                raise ModelProtocolError(
                    "coding_provider_budget_exceeded",
                    "Coding provider call budget exceeded.",
                )
            state.provider_calls_used += 1
            attempt_context = projected
            if attempt:
                attempt_context = _append_user(projected, _correction(last_error))
            response = await collect_model_stream(
                self.provider, attempt_context, settings
            )
            total_usage = _merge_usage(total_usage, response.usage)
            try:
                normalized = _normalize_response(response)
            except ModelProtocolError as exc:
                last_error = exc
                if attempt == self.max_protocol_retries:
                    raise
                continue
            if reset_notice:
                setattr(self.worker_client, "pending_reset_notice", False)
            async for event in _response_events(normalized, total_usage):
                yield event
            return
        raise AssertionError("unreachable")


def _normalize_response(response: ModelResponse) -> ModelResponse:
    if response.message.tool_calls:
        raise ModelProtocolError(
            "coding_provider_tool_call_not_allowed",
            "Coding providers must not emit native ToolCalls.",
        )
    if response.finish_reason in {FinishReason.LENGTH, FinishReason.CONTENT_FILTER}:
        raise ModelProtocolError(
            "coding_provider_incomplete_response",
            "Incomplete provider responses cannot execute Python.",
        )
    parts = response.message.content
    if any(not isinstance(item, TextContent) for item in parts):
        raise ModelProtocolError(
            "unsupported_coding_provider_output", "Coding provider output must be text."
        )
    text_parts = tuple(item for item in parts if isinstance(item, TextContent))
    text = "".join(item.text for item in text_parts)
    try:
        parsed = parse_python_fence(text)
    except CodingProtocolError as exc:
        raise ModelProtocolError(exc.code, str(exc)) from exc
    if parsed.kind == "text":
        return ModelResponse(AssistantMessage(text), FinishReason.STOP, response.usage)
    assert parsed.code is not None
    call = ToolCall(
        uuid4().hex, "execute_python", FrozenJsonObject({"code": parsed.code})
    )
    return ModelResponse(
        AssistantMessage(parsed.text, (call,)), FinishReason.TOOL_CALL, response.usage
    )


async def _response_events(
    response: ModelResponse, usage: Usage | None
) -> AsyncIterator[ModelEvent]:
    sequence = 0
    yield ResponseStarted(uuid4().hex, sequence)
    sequence += 1
    for content in response.message.content:
        if isinstance(content, TextContent) and content.text:
            yield TextDelta(sequence, content.text)
            sequence += 1
    for index, call in enumerate(response.message.tool_calls):
        yield ToolCallStarted(sequence, index, call.id, call.name)
        sequence += 1
        arguments = canonical_json_dumps(thaw_json(call.arguments))
        yield ToolCallArgumentsDelta(sequence, index, call.id, arguments)
        sequence += 1
        yield ToolCallCompleted(sequence, index, call)
        sequence += 1
    if usage is not None:
        yield UsageUpdated(sequence, usage)
        sequence += 1
    yield ResponseCompleted(
        sequence, ModelResponse(response.message, response.finish_reason, usage)
    )


def _project_context(context: ModelContext) -> ModelContext:
    segments: list[ModelContextSegment] = []
    pending_python: str | None = None
    for segment in context.segments:
        if isinstance(segment, SummarySegment):
            segments.append(segment)
            continue
        if isinstance(segment, WorkspaceReferenceSegment):
            metadata = f"Workspace reference:\nuri: {segment.uri}"
            if segment.media_type:
                metadata += f"\nmedia_type: {segment.media_type}"
            if segment.preview:
                metadata += f"\npreview: {segment.preview}"
            segments.append(MessageSegment(UserMessage(metadata)))
            continue
        if not isinstance(segment, MessageSegment):
            raise ModelProtocolError(
                "unsupported_coding_context_segment",
                "Unsupported coding context segment.",
            )
        message = segment.message
        if isinstance(message, AssistantMessage) and len(message.tool_calls) == 1:
            call = message.tool_calls[0]
            if call.name == "execute_python":
                code = call.arguments.get("code")
                if not isinstance(code, str):
                    raise ModelProtocolError(
                        "invalid_execute_python_exchange",
                        "Invalid execute_python call.",
                    )
                reasoning = _message_text(message)
                prefix = f"{reasoning}\n" if reasoning else ""
                segments.append(
                    MessageSegment(
                        AssistantMessage(
                            f"{prefix}Python action:\n{_projected_python_fence(code)}"
                        )
                    )
                )
                pending_python = call.id
                continue
        if (
            isinstance(message, ToolResultMessage)
            and pending_python == message.tool_call_id
        ):
            segments.append(
                MessageSegment(UserMessage(_observation(message) + _ACTION_REMINDER))
            )
            pending_python = None
            continue
        segments.append(segment)
    return ModelContext(
        context.system_prompt, tuple(segments), (), context.recent_tail_complete
    )


def _observation(message: ToolResultMessage) -> str:
    if message.status is ToolResultStatus.ERROR:
        error = message.error
        assert isinstance(error, ToolErrorInfo)
        return (
            "Observation (tool_error):\n"
            f"code: {error.code}\nretryable: {str(error.retryable).lower()}\nmessage: {error.message}"
        )
    rendered: list[str] = []
    for index, content in enumerate(message.content, 1):
        if isinstance(content, TextContent):
            value = f"Observation (text):\n{content.text}"
        elif isinstance(content, JsonContent):
            value = (
                f"Observation (json):\n{canonical_json_dumps(thaw_json(content.value))}"
            )
        elif isinstance(content, ArtifactReferenceContent):
            value = (
                "Observation (artifact):\n"
                f"uri: {content.uri}\nmedia_type: {content.media_type}\nsize: {content.size}\n"
                f"digest: {content.digest}\npreview: {content.preview}"
            )
        else:
            raise ModelProtocolError(
                "unsupported_coding_context_segment", "Unsupported Tool observation."
            )
        if len(message.content) > 1:
            value = f"Observation block {index}:\n{value}"
        rendered.append(value)
    return "\n\n".join(rendered) or "Observation (text):\n"


def _local_final(context: ModelContext) -> ModelResponse | None:
    if not context.segments:
        return None
    final_segment = context.segments[-1]
    if not isinstance(final_segment, MessageSegment) or not isinstance(
        final_segment.message, ToolResultMessage
    ):
        return None
    result = final_segment.message
    if (
        result.tool_name != "execute_python"
        or result.status is not ToolResultStatus.SUCCESS
    ):
        return None
    if not context.recent_tail_complete:
        raise ModelProtocolError(
            "coding_context_tail_unavailable",
            "Recent context tail is unavailable for local final recognition.",
        )
    if len(result.content) != 1 or not isinstance(result.content[0], JsonContent):
        return None
    envelope = thaw_json(result.content[0].value)
    if (
        not isinstance(envelope, dict)
        or envelope.get("protocol") != EXECUTE_PROTOCOL
        or envelope.get("is_final") is not True
    ):
        return None
    try:
        final = validate_final_value(envelope.get("final"))
    except CodingProtocolError as exc:
        raise ModelProtocolError(exc.code, str(exc)) from exc
    kind, value = final["kind"], final["value"]
    if kind == "empty":
        message = AssistantMessage()
    elif kind == "text":
        message = AssistantMessage(str(value))
    elif kind == "json":
        message = AssistantMessage((JsonContent(freeze_json(value)),))
    else:
        assert isinstance(value, dict)
        message = AssistantMessage((ArtifactReferenceContent(**value),))
    return ModelResponse(message, FinishReason.STOP, Usage(0, 0, 0))


def _message_text(message: AssistantMessage) -> str:
    return "".join(
        content.text for content in message.content if isinstance(content, TextContent)
    )


def _projected_python_fence(code: str) -> str:
    longest = max((len(match.group()) for match in re.finditer(r"`+", code)), default=0)
    marker = "`" * max(3, longest + 1)
    return f"{marker}python\n{code.rstrip()}\n{marker}"


def _append_user(context: ModelContext, text: str) -> ModelContext:
    return ModelContext(
        context.system_prompt,
        (*context.segments, MessageSegment(UserMessage(text))),
        (),
        context.recent_tail_complete,
    )


def _correction(error: ModelProtocolError | None) -> str:
    code = "invalid_coding_response" if error is None else error.code
    return f"Protocol correction ({code}): return plain final text or exactly one closed ```python block."


def _merge_usage(left: Usage | None, right: Usage | None) -> Usage | None:
    if right is None:
        return left
    if left is None:
        return right

    def add(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)

    return Usage(
        add(left.input_tokens, right.input_tokens),
        add(left.output_tokens, right.output_tokens),
        add(left.total_tokens, right.total_tokens),
    )
