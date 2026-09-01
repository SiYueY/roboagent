"""Context selection with tool-call-safe message grouping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from roboagent.runtime import (
    AssistantMessage,
    CancellationToken,
    Message,
    ModelContext,
    ToolDefinition,
    ToolResultMessage,
)


class ContextManager(Protocol):
    """Build one model-facing context without changing session history."""

    async def prepare(
        self,
        *,
        system_prompt: str | None,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        cancellation: CancellationToken,
    ) -> ModelContext: ...


class FullContextManager:
    """Pass the complete Session transcript to the model unchanged."""

    async def prepare(
        self,
        *,
        system_prompt: str | None,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        cancellation: CancellationToken,
    ) -> ModelContext:
        return ModelContext(system_prompt, tuple(messages), tuple(tools))


class WindowContextManager:
    """Keep recent complete message groups within a message-count budget."""

    def __init__(self, *, max_messages: int = 64) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive.")
        self.max_messages = max_messages

    async def prepare(
        self,
        *,
        system_prompt: str | None,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        cancellation: CancellationToken,
    ) -> ModelContext:
        groups = _message_groups(messages)
        if len(messages) <= self.max_messages:
            selected = tuple(messages)
        else:
            selected = _select_recent_groups(groups, self.max_messages)
        return ModelContext(system_prompt, selected, tuple(tools))


def _select_recent_groups(
    groups: Sequence[tuple[Message, ...]], max_messages: int
) -> tuple[Message, ...]:
    """Select recent whole groups instead of slicing through a tool exchange."""
    selected: list[tuple[Message, ...]] = []
    selected_count = 0
    for group in reversed(groups):
        group_count = len(group)
        if selected and selected_count + group_count > max_messages:
            break
        selected.append(group)
        selected_count += group_count
        if selected_count >= max_messages:
            break
    return tuple(message for group in reversed(selected) for message in group)


def _message_groups(messages: Sequence[Message]) -> tuple[tuple[Message, ...], ...]:
    """Keep an assistant tool-call message and contiguous results together."""
    groups: list[tuple[Message, ...]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            call_ids = {call.id for call in message.tool_calls}
            result_ids: set[str] = set()
            end = index + 1
            while end < len(messages) and isinstance(messages[end], ToolResultMessage):
                result = messages[end]
                if result.tool_call_id not in call_ids or result.tool_call_id in result_ids:
                    raise ValueError("Tool result does not match the preceding assistant tool call.")
                result_ids.add(result.tool_call_id)
                end += 1
            groups.append(tuple(messages[index:end]))
            index = end
        else:
            groups.append((message,))
            index += 1
    return tuple(groups)
