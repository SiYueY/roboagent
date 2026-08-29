"""OpenAI Chat Completions-compatible provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from roboagent.runtime import AssistantMessage, CancellationToken, Message, ModelEvent, ModelRequest, ToolCall, Usage


class ChatModel(Protocol):
    model_name: str
    def stream(self, request: ModelRequest, cancellation: CancellationToken) -> AsyncIterator[ModelEvent]: ...


_RESERVED = frozenset({"model", "messages", "tools", "stream", "stream_options"})


@dataclass(slots=True)
class OpenAICompatibleChatModel:
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

    def __post_init__(self) -> None:
        if overlap := _RESERVED.intersection(self.model_kwargs):
            raise ValueError(f"model_kwargs cannot override reserved request fields: {', '.join(sorted(overlap))}")

    async def stream(self, request: ModelRequest, cancellation: CancellationToken) -> AsyncIterator[ModelEvent]:
        client = None
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, organization=self.organization,
                                 max_retries=self.max_retries or 2, timeout=self.request_timeout,
                                 default_headers=self.default_headers, default_query=self.default_query)
            stream = await client.chat.completions.create(**self._payload(request))
            yield ModelEvent("start")
            text, calls, usage, finish, seen = "", {}, Usage(), "stop", False
            async for chunk in stream:
                if cancellation.cancelled:
                    yield ModelEvent("cancelled", error="Model request cancelled.")
                    return
                seen = True
                if chunk.usage:
                    usage = Usage(chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0, chunk.usage.total_tokens or 0)
                for choice in chunk.choices:
                    finish = choice.finish_reason or finish
                    delta = choice.delta
                    if delta.content:
                        text += delta.content
                        yield ModelEvent("text_delta", delta=delta.content)
                    for call in delta.tool_calls or ():
                        item = calls.setdefault(call.index or 0, {"id": "", "name": "", "arguments": ""})
                        item["id"] = call.id or item["id"]
                        if call.function:
                            item["name"] = call.function.name or item["name"]
                            fragment = call.function.arguments or ""
                            item["arguments"] += fragment
                            if fragment:
                                yield ModelEvent("tool_call_delta", fragment, call.index)
            if not seen:
                yield ModelEvent("error", error="Model stream ended without response chunks.")
            else:
                yield ModelEvent("done", message=AssistantMessage(text, tuple(_parse(calls[i]) for i in sorted(calls)), finish, usage, self.model_name))
        except Exception as exc:
            yield ModelEvent("error", error=str(exc))
        finally:
            if client is not None:
                await client.close()

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        data: dict[str, Any] = {"model": request.model, "messages": _messages(request.context.system_prompt, request.context.messages), "stream": True, "stream_options": {"include_usage": True}}
        if request.context.tools:
            data["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": dict(t.parameters)}} for t in request.context.tools]
        for name in ("temperature", "max_tokens", "top_p", "reasoning_effort"):
            if (value := getattr(self, name)) is not None:
                data[name] = value
        data.update(self.model_kwargs)
        if self.extra_body:
            data["extra_body"] = self.extra_body
        return data


def _parse(raw: dict[str, str]) -> ToolCall:
    try:
        parsed = json.loads(raw["arguments"])
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object.")
        return ToolCall(raw["id"], raw["name"], raw["arguments"], parsed)
    except (ValueError, json.JSONDecodeError) as exc:
        return ToolCall(raw["id"], raw["name"], raw["arguments"], None, str(exc))


def _messages(system_prompt: str | None, messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    result = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for message in messages:
        if message.role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                item["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in message.tool_calls]
            result.append(item)
        elif message.role == "tool": result.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content})
        else: result.append({"role": "user", "content": message.content})
    return result


__all__ = ["ChatModel", "OpenAICompatibleChatModel"]
