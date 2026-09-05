"""Canonical model protocol, stream validation, and OpenAI-compatible adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    FrozenJsonObject,
    ImageContent,
    JsonContent,
    ProtocolError,
    TextContent,
    ToolCall,
    canonical_json_dumps,
    freeze_json_object,
    text_of,
    thaw_json,
)
from roboagent.runtime.types import (
    MediaResolutionError,
    MediaResolutionErrorCode,
    MediaResolver,
    Modality,
    ResolvedMedia,
    modality,
)
from .errors import ModelCapabilityError, ModelProtocolError, ModelProviderError

if __import__("typing").TYPE_CHECKING:
    from roboagent.context import ModelContext

_LOG = logging.getLogger(__name__)
_RESERVED_REQUEST_KEYS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "stream",
        "stream_options",
        "parallel_tool_calls",
        "temperature",
        "max_tokens",
        "top_p",
        "reasoning_effort",
        "extra_body",
    }
)


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    input_modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    output_modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    tool_calling: bool = False
    parallel_tool_calls: bool = False
    context_window: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_modalities", frozenset(self.input_modalities))
        object.__setattr__(self, "output_modalities", frozenset(self.output_modalities))
        if not all(isinstance(item, Modality) for item in self.input_modalities | self.output_modalities):
            raise TypeError("Model modalities must contain Modality values.")
        if not isinstance(self.tool_calling, bool) or not isinstance(self.parallel_tool_calls, bool):
            raise TypeError("Model capability flags must be bool.")
        if self.context_window is not None and (
            not isinstance(self.context_window, int) or isinstance(self.context_window, bool) or self.context_window < 1
        ):
            raise ValueError("context_window must be positive or None.")


@dataclass(frozen=True, slots=True)
class ModelSettings:
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    extra: FrozenJsonObject = field(default_factory=FrozenJsonObject)

    def __post_init__(self) -> None:
        if self.temperature is not None and (isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) or not math.isfinite(self.temperature)):
            raise ValueError("temperature must be finite.")
        if self.top_p is not None and (isinstance(self.top_p, bool) or not isinstance(self.top_p, (int, float)) or not 0 < self.top_p <= 1):
            raise ValueError("top_p must be in (0, 1].")
        if self.max_output_tokens is not None and (not isinstance(self.max_output_tokens, int) or isinstance(self.max_output_tokens, bool) or self.max_output_tokens < 1):
            raise ValueError("max_output_tokens must be positive.")
        object.__setattr__(self, "extra", freeze_json_object(self.extra))


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0) for value in values):
            raise ValueError("Usage values must be non-negative integers.")


class FinishReason(Enum):
    STOP = "stop"
    TOOL_CALL = "tool_call"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ModelResponse:
    message: AssistantMessage
    finish_reason: FinishReason
    usage: Usage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, AssistantMessage) or not isinstance(self.finish_reason, FinishReason):
            raise TypeError("ModelResponse requires canonical message and finish reason.")
        if self.usage is not None and not isinstance(self.usage, Usage):
            raise TypeError("ModelResponse usage must be Usage or None.")


@dataclass(frozen=True, slots=True)
class ResponseStarted:
    response_id: str
    sequence: int = 0

    def __post_init__(self) -> None:
        _stream_identity(self.sequence)
        if not isinstance(self.response_id, str) or not self.response_id:
            raise ValueError("response_id must be non-empty.")


@dataclass(frozen=True, slots=True)
class TextDelta:
    sequence: int
    text: str

    def __post_init__(self) -> None:
        _stream_identity(self.sequence)
        if not isinstance(self.text, str):
            raise TypeError("TextDelta.text must be str.")


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    sequence: int
    call_index: int
    call_id: str
    name: str | None = None

    def __post_init__(self) -> None:
        _stream_identity(self.sequence, self.call_index)
        if not isinstance(self.call_id, str) or not self.call_id:
            raise ValueError("ToolCallStarted requires call_id.")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("ToolCallStarted.name must be non-empty or None.")


@dataclass(frozen=True, slots=True)
class ToolCallArgumentsDelta:
    sequence: int
    call_index: int
    call_id: str
    delta: str

    def __post_init__(self) -> None:
        _stream_identity(self.sequence, self.call_index)
        if not isinstance(self.call_id, str) or not self.call_id or not isinstance(self.delta, str):
            raise ValueError("Invalid ToolCall arguments delta.")


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    sequence: int
    call_index: int
    call: ToolCall

    def __post_init__(self) -> None:
        _stream_identity(self.sequence, self.call_index)
        if not isinstance(self.call, ToolCall):
            raise TypeError("ToolCallCompleted.call must be ToolCall.")


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    sequence: int
    usage: Usage

    def __post_init__(self) -> None:
        _stream_identity(self.sequence)
        if not isinstance(self.usage, Usage):
            raise TypeError("UsageUpdated.usage must be Usage.")


@dataclass(frozen=True, slots=True)
class ResponseCompleted:
    sequence: int
    response: ModelResponse

    def __post_init__(self) -> None:
        _stream_identity(self.sequence)
        if not isinstance(self.response, ModelResponse):
            raise TypeError("ResponseCompleted.response must be ModelResponse.")


ModelEvent = (
    ResponseStarted
    | TextDelta
    | ToolCallStarted
    | ToolCallArgumentsDelta
    | ToolCallCompleted
    | UsageUpdated
    | ResponseCompleted
)


def _stream_identity(sequence: int, call_index: int | None = None) -> None:
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("Model event sequence must be a non-negative integer.")
    if call_index is not None and (not isinstance(call_index, int) or isinstance(call_index, bool) or call_index < 0):
        raise ValueError("Tool call index must be a non-negative integer.")


class Model(Protocol):
    @property
    def capabilities(self) -> ModelCapabilities: ...

    def stream(self, context: "ModelContext", settings: ModelSettings | None = None) -> AsyncIterator[ModelEvent]: ...


class ModelProvider(Protocol):
    def get_model(self, name: str) -> Model: ...

    async def close(self) -> None: ...


def _event_sequence(event: ModelEvent) -> int:
    return event.sequence


def validate_model_context(capabilities: ModelCapabilities, context: "ModelContext") -> None:
    from roboagent.context import MessageSegment, SummarySegment, WorkspaceReferenceSegment

    for segment in context.segments:
        if isinstance(segment, MessageSegment):
            for content in segment.message.content:
                if modality(content) not in capabilities.input_modalities:
                    raise ModelCapabilityError("unsupported_input_modality", "Model does not support an input modality.")
        elif not isinstance(segment, (SummarySegment, WorkspaceReferenceSegment)):
            raise ModelProtocolError("invalid_model_context", "Unknown ModelContext segment.")
    if context.tools and not capabilities.tool_calling:
        raise ModelCapabilityError("model_does_not_support_tools", "Model does not support tools.")


async def collect_model_stream(
    model: Model,
    context: "ModelContext",
    settings: ModelSettings | None = None,
    on_event: object | None = None,
) -> ModelResponse:
    """Collect and strictly validate one canonical model stream."""
    if not isinstance(model.capabilities, ModelCapabilities):
        raise ModelCapabilityError("invalid_model_capabilities", "Model capabilities are not canonical.")
    validate_model_context(model.capabilities, context)
    iterator = model.stream(context, settings)
    if not hasattr(iterator, "__aiter__"):
        close_coroutine = getattr(iterator, "close", None)
        if close_coroutine is not None:
            close_coroutine()
        raise ModelProtocolError("invalid_provider_response", "Model.stream must return an AsyncIterator.")
    expected_sequence = 0
    started = False
    completed: ModelResponse | None = None
    calls: dict[int, ToolCall] = {}
    call_states: dict[int, tuple[str, str | None, bool]] = {}
    call_ids: set[str] = set()
    latest_usage: Usage | None = None
    try:
        async for event in iterator:
            if not isinstance(event, (ResponseStarted, TextDelta, ToolCallStarted, ToolCallArgumentsDelta, ToolCallCompleted, UsageUpdated, ResponseCompleted)):
                raise ModelProtocolError("invalid_stream_sequence", "Unknown canonical model event.")
            if _event_sequence(event) != expected_sequence:
                raise ModelProtocolError("invalid_stream_sequence", "Model event sequence is not contiguous.")
            expected_sequence += 1
            if completed is not None:
                raise ModelProtocolError("invalid_stream_sequence", "No events may follow ResponseCompleted.")
            if isinstance(event, ResponseStarted):
                if started:
                    raise ModelProtocolError("duplicate_response_started", "ResponseStarted occurred twice.")
                if event.sequence != 0:
                    raise ModelProtocolError("invalid_stream_sequence", "ResponseStarted must be first.")
                started = True
            elif not started:
                raise ModelProtocolError("invalid_stream_sequence", "ResponseStarted must be first.")
            elif isinstance(event, ToolCallStarted):
                if event.call_index in call_states:
                    raise ModelProtocolError("duplicate_tool_call_started", "ToolCall started twice.")
                if event.call_id in call_ids:
                    raise ModelProtocolError("duplicate_tool_call_id", "ToolCall IDs must be unique.")
                call_ids.add(event.call_id)
                call_states[event.call_index] = (event.call_id, event.name, False)
            elif isinstance(event, ToolCallArgumentsDelta):
                state = call_states.get(event.call_index)
                if state is None or state[2] or state[0] != event.call_id:
                    raise ModelProtocolError("invalid_tool_call_delta_state", "ToolCall argument delta is out of state.")
            elif isinstance(event, ToolCallCompleted):
                state = call_states.get(event.call_index)
                if state is None:
                    raise ModelProtocolError("invalid_tool_call_delta_state", "ToolCall completed before it started.")
                if state[2] or event.call_index in calls:
                    raise ModelProtocolError("duplicate_tool_call_completed", "ToolCall completed twice.")
                if state[0] != event.call.id or state[1] is not None and state[1] != event.call.name:
                    raise ModelProtocolError("invalid_tool_call_delta_state", "Completed ToolCall identity changed.")
                call_states[event.call_index] = (state[0], state[1], True)
                calls[event.call_index] = event.call
            elif isinstance(event, UsageUpdated):
                latest_usage = event.usage
            elif isinstance(event, ResponseCompleted):
                if completed is not None:
                    raise ModelProtocolError("invalid_stream_sequence", "ResponseCompleted occurred twice.")
                if any(not state[2] for state in call_states.values()):
                    raise ModelProtocolError("incomplete_tool_call", "ResponseCompleted has an incomplete ToolCall.")
                expected_calls = tuple(call for _, call in sorted(calls.items()))
                if event.response.message.tool_calls != expected_calls:
                    raise ModelProtocolError("tool_call_response_mismatch", "Final ToolCalls differ from stream events.")
                if latest_usage is not None and event.response.usage != latest_usage:
                    raise ModelProtocolError("invalid_provider_response", "Final usage differs from latest usage event.")
                completed = event.response
            if on_event is not None:
                observed = on_event(event)  # type: ignore[operator]
                if inspect.isawaitable(observed):
                    await observed
        if not started or completed is None:
            raise ModelProtocolError("missing_terminal_response", "Model stream ended without ResponseCompleted.")
        if any(not state[2] for state in call_states.values()):
            raise ModelProtocolError("incomplete_tool_call", "Model stream ended with an incomplete ToolCall.")
        if any(modality(part) not in model.capabilities.output_modalities for part in completed.message.content):
            raise ModelCapabilityError("unsupported_output_modality", "Model emitted an unsupported modality.")
        if completed.message.tool_calls and not model.capabilities.tool_calling:
            raise ModelCapabilityError("model_does_not_support_tools", "Model emitted ToolCalls without tool capability.")
        if not model.capabilities.parallel_tool_calls and len(completed.message.tool_calls) > 1:
            raise ModelCapabilityError("parallel_tool_calls_unsupported", "Model emitted parallel ToolCalls.")
        return completed
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


@dataclass(slots=True)
class OpenAICompatibleModel:
    model_name: str
    api_key: str | None = None
    base_url: str | None = None
    organization: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_retries: int | None = None
    request_timeout: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] | None = None
    default_headers: dict[str, str] | None = None
    default_query: dict[str, Any] | None = None
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    media_resolver: MediaResolver | None = None
    client: object | None = None
    capabilities: ModelCapabilities = field(
        default_factory=lambda: ModelCapabilities(
            frozenset({Modality.TEXT, Modality.IMAGE}),
            frozenset({Modality.TEXT}),
            True,
            True,
        )
    )

    async def stream(self, context: "ModelContext", settings: ModelSettings | None = None) -> AsyncIterator[ModelEvent]:
        validate_model_context(self.capabilities, context)
        owned_client = self.client is None
        client = self.client
        resources: list[ResolvedMedia] = []
        stream: object | None = None
        try:
            if client is None:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    organization=self.organization,
                    max_retries=2 if self.max_retries is None else self.max_retries,
                    timeout=self.request_timeout,
                    default_headers=self.default_headers,
                    default_query=self.default_query,
                )
            messages, resources = await _messages(context, self.media_resolver)
            effective = ModelSettings(
                temperature=settings.temperature if settings and settings.temperature is not None else self.temperature,
                max_output_tokens=settings.max_output_tokens if settings and settings.max_output_tokens is not None else self.max_tokens,
                top_p=settings.top_p if settings and settings.top_p is not None else self.top_p,
                extra=settings.extra if settings else FrozenJsonObject(),
            )
            payload: dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": thaw_json(tool.input_schema),
                        },
                    }
                    for tool in context.tools
                ] or None,
                "stream": True,
                "stream_options": {"include_usage": True},
                "parallel_tool_calls": self.capabilities.parallel_tool_calls if context.tools else None,
            }
            if effective.temperature is not None:
                payload["temperature"] = effective.temperature
            if effective.max_output_tokens is not None:
                payload["max_tokens"] = effective.max_output_tokens
            if effective.top_p is not None:
                payload["top_p"] = effective.top_p
            if self.reasoning_effort is not None:
                payload["reasoning_effort"] = self.reasoning_effort
            extras = {**self.model_kwargs, **thaw_json(effective.extra)}
            overlap = _RESERVED_REQUEST_KEYS.intersection(extras)
            if overlap:
                raise ModelProtocolError(
                    "invalid_provider_response",
                    f"Provider extras cannot override canonical request fields: {', '.join(sorted(overlap))}.",
                )
            payload.update(extras)
            if self.extra_body:
                payload["extra_body"] = self.extra_body
            stream = await client.chat.completions.create(**payload)  # type: ignore[union-attr]
            async for event in _stream_chunks(stream, self.model_name):
                yield event
        except asyncio.CancelledError:
            raise
        except (ModelCapabilityError, ModelProtocolError, MediaResolutionError):
            raise
        except Exception as exc:
            raise ModelProviderError("provider_error", "Provider request failed.") from exc
        finally:
            close_stream = getattr(stream, "aclose", None)
            if close_stream is not None:
                await close_stream()
            for resource in resources:
                try:
                    await resource.close()
                except Exception as exc:
                    _LOG.warning("Could not release model media resource (%s)", type(exc).__name__)
            if owned_client and client is not None:
                await client.close()  # type: ignore[union-attr]


async def _stream_chunks(stream: object, model_name: str) -> AsyncIterator[ModelEvent]:
    sequence = 0
    response_id = uuid4().hex
    yield ResponseStarted(response_id, sequence)
    sequence += 1
    text_parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    finish_reason = FinishReason.OTHER
    usage: Usage | None = None
    async for chunk in stream:  # type: ignore[union-attr]
        chunk_id = getattr(chunk, "id", None)
        if chunk_id and response_id != chunk_id and not text_parts and not calls:
            response_id = chunk_id
        raw_usage = getattr(chunk, "usage", None)
        if raw_usage is not None:
            usage = Usage(
                getattr(raw_usage, "prompt_tokens", None),
                getattr(raw_usage, "completion_tokens", None),
                getattr(raw_usage, "total_tokens", None),
            )
            yield UsageUpdated(sequence, usage)
            sequence += 1
        for choice in getattr(chunk, "choices", ()):
            reason = getattr(choice, "finish_reason", None)
            if reason is not None:
                finish_reason = _finish_reason(reason)
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                yield TextDelta(sequence, delta.content)
                sequence += 1
            for fragment in delta.tool_calls or ():
                index = fragment.index
                if index not in calls:
                    call_id = fragment.id or f"{model_name}:tool:{index}:{uuid4().hex[:8]}"
                    name = fragment.function.name if fragment.function else None
                    calls[index] = {"id": call_id, "name": name or "", "arguments": ""}
                    yield ToolCallStarted(sequence, index, call_id, name)
                    sequence += 1
                state = calls[index]
                if fragment.id and fragment.id != state["id"]:
                    raise ModelProtocolError("invalid_tool_call_delta_state", "ToolCall ID changed during streaming.")
                if fragment.function:
                    if fragment.function.name:
                        if state["name"] and state["name"] != fragment.function.name:
                            raise ModelProtocolError("invalid_tool_call_delta_state", "Tool name changed during streaming.")
                        state["name"] = fragment.function.name
                    piece = fragment.function.arguments or ""
                    if piece:
                        state["arguments"] += piece
                        yield ToolCallArgumentsDelta(sequence, index, state["id"], piece)
                        sequence += 1
    normalized: list[ToolCall] = []
    ids: set[str] = set()
    for index in sorted(calls):
        state = calls[index]
        if not state["name"]:
            raise ModelProtocolError("incomplete_tool_call", "ToolCall has no name.")
        try:
            arguments = json.loads(state["arguments"] or "{}")
        except (json.JSONDecodeError, ProtocolError) as exc:
            raise ModelProtocolError("invalid_tool_arguments", "Tool arguments are invalid JSON.") from exc
        if not isinstance(arguments, dict):
            raise ModelProtocolError("invalid_tool_arguments", "Tool arguments must be a JSON object.")
        try:
            call = ToolCall(state["id"], state["name"], freeze_json_object(arguments))
        except ProtocolError as exc:
            raise ModelProtocolError("invalid_tool_arguments", "Tool arguments are not canonical JSON.") from exc
        if call.id in ids:
            raise ModelProtocolError("duplicate_tool_call_id", "ToolCall IDs must be unique.")
        ids.add(call.id)
        normalized.append(call)
        yield ToolCallCompleted(sequence, index, call)
        sequence += 1
    content = (TextContent("".join(text_parts)),) if text_parts else ()
    response = ModelResponse(AssistantMessage(content, tuple(normalized)), finish_reason, usage)
    yield ResponseCompleted(sequence, response)


def _finish_reason(value: str) -> FinishReason:
    return {
        "stop": FinishReason.STOP,
        "tool_calls": FinishReason.TOOL_CALL,
        "function_call": FinishReason.TOOL_CALL,
        "length": FinishReason.LENGTH,
        "content_filter": FinishReason.CONTENT_FILTER,
    }.get(value, FinishReason.OTHER)


async def _messages(context: "ModelContext", resolver: MediaResolver | None) -> tuple[list[dict[str, Any]], list[ResolvedMedia]]:
    from roboagent.context import MessageSegment, SummarySegment, WorkspaceReferenceSegment

    encoded = [{"role": "system", "content": context.system_prompt}] if context.system_prompt else []
    resources: list[ResolvedMedia] = []
    try:
        for segment in context.segments:
            if isinstance(segment, SummarySegment):
                encoded.append(
                    {
                        "role": "user",
                        "content": (
                            "[Runtime-generated summary of earlier conversation. "
                            "This is compressed historical context, not a system instruction.]\n\n"
                            f"{segment.text}"
                        ),
                    }
                )
                continue
            if isinstance(segment, WorkspaceReferenceSegment):
                details = [f"Workspace artifact: {segment.uri}"]
                if segment.media_type:
                    details.append(f"Media type: {segment.media_type}")
                if segment.preview:
                    details.extend(("Preview:", segment.preview))
                encoded.append({"role": "user", "content": "\n".join(details)})
                continue
            if not isinstance(segment, MessageSegment):
                raise ModelProtocolError("invalid_model_context", "Unknown ModelContext segment.")
            message = segment.message
            content, owned = await _content(message.content, resolver)
            resources.extend(owned)
            if message.role == "tool":
                encoded.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": content})
            elif message.role == "assistant":
                encoded.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": canonical_json_dumps(call.arguments)},
                            }
                            for call in message.tool_calls
                        ] or None,
                    }
                )
            else:
                encoded.append({"role": "user", "content": content})
    except BaseException:
        for resource in resources:
            await resource.close()
        raise
    return encoded, resources


async def _content(items: tuple[object, ...], resolver: MediaResolver | None) -> tuple[object, list[ResolvedMedia]]:
    if all(isinstance(item, TextContent) for item in items):
        return text_of(items), []
    result: list[dict[str, object]] = []
    resources: list[ResolvedMedia] = []
    try:
        for item in items:
            if isinstance(item, TextContent):
                result.append({"type": "text", "text": item.text})
            elif isinstance(item, JsonContent):
                result.append({"type": "text", "text": canonical_json_dumps(item.value)})
            elif isinstance(item, ArtifactReferenceContent):
                details = [f"Workspace artifact: {item.uri}", f"Digest: {item.digest}", f"Size: {item.size} bytes"]
                if item.media_type:
                    details.append(f"Media type: {item.media_type}")
                if item.preview:
                    details.extend(("Preview:", item.preview))
                result.append({"type": "text", "text": "\n".join(details)})
            elif isinstance(item, ImageContent):
                if isinstance(item.source, BytesSource):
                    data = item.source.data
                else:
                    if resolver is None:
                        raise MediaResolutionError(MediaResolutionErrorCode.ACCESS_DENIED, "External media requires a resolver.")
                    resource = await resolver.resolve(item.source, expected_media_type=item.media_type, cancellation=_NoCancellation())
                    resources.append(resource)
                    if item.media_type and resource.media_type and item.media_type != resource.media_type:
                        raise MediaResolutionError(MediaResolutionErrorCode.MEDIA_TYPE_MISMATCH, "Resolved media type differs.")
                    data = resource.payload if isinstance(resource.payload, bytes) else resource.payload.read_bytes()
                result.append({"type": "image_url", "image_url": {"url": f"data:{item.media_type or 'image/png'};base64,{base64.b64encode(data).decode()}"}})
            elif isinstance(item, (AudioContent, FileContent)):
                raise ModelCapabilityError("unsupported_input_modality", "This provider adapter cannot encode this modality.")
            else:
                raise ModelProtocolError("invalid_provider_response", "Unknown message content.")
    except BaseException:
        for resource in resources:
            await resource.close()
        raise
    return result, resources


class _NoCancellation:
    @property
    def cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None

    async def wait_cancelled(self) -> None:
        await asyncio.Future()
