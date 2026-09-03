"""Provider-neutral model protocol and the OpenAI Chat Completions adapter."""
from __future__ import annotations
import asyncio
import base64, json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from roboagent.message import AssistantMessage, BytesSource, ImageContent, TextContent, ToolCall, text_of
from roboagent.runtime.types import (CancellationToken, MediaResolutionError, MediaResolutionErrorCode,
    ModelCapabilities, ModelCompleted, ModelFailed, ModelRequest, Modality, ResolvedMedia, TextDelta,
    ToolCallDelta)

class ChatModel(Protocol):
    model_name: str; capabilities: ModelCapabilities
    def stream(self, request: ModelRequest, cancellation: CancellationToken) -> AsyncIterator[object]: ...


def _json_value(value: Any) -> Any:
    """Convert frozen canonical JSON containers to provider-native JSON values."""
    if isinstance(value, Mapping): return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, tuple): return [_json_value(child) for child in value]
    return value

@dataclass(slots=True)
class OpenAICompatibleChatModel:
    model_name: str; api_key: str | None = None; base_url: str | None = None; organization: str | None = None
    temperature: float | None = None; max_tokens: int | None = None; max_retries: int | None = None; request_timeout: float | None = None
    top_p: float | None = None; reasoning_effort: str | None = None; extra_body: dict[str, Any] | None = None
    default_headers: dict[str, str] | None = None; default_query: dict[str, Any] | None = None; model_kwargs: dict[str, Any] = field(default_factory=dict)
    capabilities: ModelCapabilities = field(default_factory=lambda: ModelCapabilities(frozenset({Modality.TEXT, Modality.IMAGE}), frozenset({Modality.TEXT}), frozenset({Modality.TEXT, Modality.IMAGE}), True))
    async def stream(self, request: ModelRequest, cancellation: CancellationToken) -> AsyncIterator[object]:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, organization=self.organization, max_retries=self.max_retries or 2, timeout=self.request_timeout, default_headers=self.default_headers, default_query=self.default_query)
            try:
                resources: list[ResolvedMedia] = []
                messages, resources = await _messages(request)
                try:
                    payload: dict[str, Any] = {"model":self.model_name, "messages":messages, "tools":[{"type":"function","function":{"name":t.name,"description":t.description,"parameters":_json_value(t.parameters)}} for t in request.context.tools] or None, "stream":True}
                    for name in ("temperature", "max_tokens", "top_p", "reasoning_effort"):
                        if (value := getattr(self, name)) is not None: payload[name] = value
                    payload.update(self.model_kwargs)
                    if self.extra_body: payload["extra_body"] = self.extra_body
                    stream = await client.chat.completions.create(**payload)
                    async for item in _stream_chunks(stream, cancellation, self.model_name): yield item
                finally:
                    for resource in resources:
                        try: await resource.close()
                        except Exception: pass
            finally: await client.close()
        except asyncio.CancelledError:
            raise
        except MediaResolutionError:
            raise
        except Exception as exc:
            # Surface only stable, non-secret diagnostics.  Provider response
            # bodies may contain prompts, media URLs, or credentials and must
            # never cross the canonical model-event boundary.
            status = getattr(exc, "status_code", None)
            suffix = f"_{status}" if isinstance(status, int) else ""
            yield ModelFailed(f"provider_{type(exc).__name__.lower()}{suffix}")

async def _stream_chunks(stream: object, cancellation: CancellationToken, model_name: str) -> AsyncIterator[object]:
                text = ""; calls: dict[int, dict[str, str]] = {}
                async for chunk in stream:  # type: ignore[union-attr]
                    if cancellation.cancelled: yield ModelFailed("cancelled"); return
                    for choice in chunk.choices:
                        delta = choice.delta
                        if delta.content: text += delta.content; yield TextDelta(delta.content)
                        for call in delta.tool_calls or ():
                            data = calls.setdefault(call.index, {"id":"", "name":"", "arguments":""})
                            data["id"] = call.id or data["id"]
                            if call.function:
                                data["name"] = call.function.name or data["name"]; fragment = call.function.arguments or ""; data["arguments"] += fragment
                                yield ToolCallDelta(call.index, call.id, call.function.name, fragment)
                normalized = []
                for index in sorted(calls):
                    raw = calls[index]
                    if not raw["id"]:
                        # Provider identity is optional.  The adapter, rather
                        # than AgentLoop, owns this deterministic normalization.
                        raw["id"] = f"{model_name}:tool:{index}"
                        yield ToolCallDelta(index, call_id=raw["id"])
                    args = json.loads(raw["arguments"])
                    normalized.append(ToolCall(raw["id"], raw["name"], raw["arguments"], args))
                yield ModelCompleted(AssistantMessage((TextContent(text),) if text else (), tuple(normalized), model=model_name))

async def _messages(request: ModelRequest) -> tuple[list[dict[str, Any]], list[object]]:
    encoded = [{"role":"system", "content":request.context.system_prompt}] if request.context.system_prompt else []; resources=[]
    try:
        for message in request.context.messages:
            content, owned = await _content(message.content, request); resources.extend(owned)
            if message.role == "tool": encoded.append({"role":"tool", "tool_call_id":message.tool_call_id, "content":content})
            elif message.role == "assistant": encoded.append({"role":"assistant", "content":content or None, "tool_calls":[{"id":c.id,"type":"function","function":{"name":c.name,"arguments":c.raw_arguments}} for c in message.tool_calls] or None})
            else: encoded.append({"role":"user", "content":content})
    except BaseException:
        for resource in resources:
            await resource.close()
        raise
    return encoded, resources
async def _content(items: tuple[object, ...], request: ModelRequest) -> tuple[object, list[object]]:
    if all(isinstance(x, TextContent) for x in items): return text_of(items), []
    request.run_context.cancellation.throw_if_cancelled()
    external = [(index, item) for index, item in enumerate(items) if isinstance(item, ImageContent) and not isinstance(item.source, BytesSource)]
    resolved_by_index: dict[int, ResolvedMedia] = {}
    if external:
        if request.media_resolver is None:
            raise MediaResolutionError(MediaResolutionErrorCode.ACCESS_DENIED, "External media requires a MediaResolver.")

        async def resolve(index: int, item: ImageContent) -> tuple[int, ResolvedMedia]:
            request.run_context.cancellation.throw_if_cancelled()
            try:
                resolved = await request.media_resolver.resolve(
                    item.source,
                    expected_media_type=item.media_type,
                    run_context=request.run_context,
                    cancellation=request.run_context.cancellation,
                )
            except asyncio.CancelledError:
                raise
            except MediaResolutionError:
                raise
            except Exception as exc:
                raise MediaResolutionError(MediaResolutionErrorCode.FETCH_FAILED, "External media resolution failed.") from exc
            request.run_context.cancellation.throw_if_cancelled()
            if item.media_type is not None and resolved.media_type is not None and item.media_type != resolved.media_type:
                await resolved.close()
                raise MediaResolutionError(MediaResolutionErrorCode.MEDIA_TYPE_MISMATCH, "Resolved media type differs from canonical media type.")
            return index, resolved

        pending = {
            asyncio.create_task(resolve(index, item))
            for index, item in external
        }
        successful: list[tuple[int, ResolvedMedia]] = []
        primary_failure: BaseException | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        successful.append(task.result())
                    except BaseException as exc:
                        # Once an ordinary failure is observed, stop all
                        # sibling resolutions before releasing successful
                        # one-shot resources.  A run cancellation checked
                        # below still takes precedence over this failure.
                        if primary_failure is None:
                            primary_failure = exc
                if primary_failure is not None:
                    for task in pending:
                        task.cancel()
                    settled = await asyncio.gather(*pending, return_exceptions=True)
                    for value in settled:
                        if not isinstance(value, BaseException):
                            successful.append(value)
                    pending.clear()
                    request.run_context.cancellation.throw_if_cancelled()
                    raise primary_failure
            resolved_by_index = {index: media for index, media in successful}
        except BaseException:
            for task in pending:
                task.cancel()
            if pending:
                settled = await asyncio.gather(*pending, return_exceptions=True)
                for value in settled:
                    if not isinstance(value, BaseException):
                        successful.append(value)
            for _index, resource in successful:
                await resource.close()
            raise

    result: list[dict[str, object]] = []
    resources: list[ResolvedMedia] = []
    try:
        for index, item in enumerate(items):
            if isinstance(item, TextContent):
                result.append({"type":"text", "text":item.text})
            elif isinstance(item, ImageContent):
                if isinstance(item.source, BytesSource):
                    data = item.source.data
                else:
                    resolved = resolved_by_index[index]
                    resources.append(resolved)
                    data = resolved.payload if isinstance(resolved.payload, bytes) else resolved.payload.read_bytes()
                result.append({"type":"image_url", "image_url":{"url":f"data:{item.media_type or 'image/png'};base64,{base64.b64encode(data).decode()}"}})
            else:
                raise ValueError("This adapter only supports image content.")
    except BaseException:
        for resource in resources:
            await resource.close()
        for index, resource in resolved_by_index.items():
            if resource not in resources:
                await resource.close()
        raise
    return result, resources
