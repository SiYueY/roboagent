"""Context selection with tool-call-safe message grouping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from roboagent.context.context import AgentContext, ContextResult, SessionContextState
from roboagent.runtime import AssistantMessage, CancellationToken, Message, ToolResultMessage


class ContextManager(Protocol):
    """Derive the context for a model call without changing session history."""

    async def prepare(
        self,
        messages: Sequence[Message],
        state: SessionContextState,
        cancellation: CancellationToken,
    ) -> ContextResult: ...


class DefaultContextManager:
    """Keep all short histories and safely window long histories by message group."""

    def __init__(self, *, max_messages: int = 64, keep_recent: int = 24) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive.")
        if keep_recent < 1:
            raise ValueError("keep_recent must be positive.")
        if keep_recent > max_messages:
            raise ValueError("keep_recent cannot exceed max_messages.")
        self.max_messages = max_messages
        self.keep_recent = keep_recent

    async def prepare(
        self,
        messages: Sequence[Message],
        state: SessionContextState,
        cancellation: CancellationToken,
    ) -> ContextResult:
        """Return a non-mutating working view of ``messages``.

        The first implementation intentionally does no compaction.  Its state is
        returned unchanged so a later compactor can advance it without changing
        the manager or run lifecycle contract.
        """
        groups = _valid_message_groups(messages)
        active_count = sum(len(group) for group in groups)
        if cancellation.cancelled or active_count <= self.max_messages:
            selected = tuple(message for group in groups for message in group)
        else:
            selected = _select_recent_groups(groups, self.keep_recent)
        return ContextResult(AgentContext(selected, state.summary), state)


def _select_recent_groups(
    groups: Sequence[tuple[Message, ...]], keep_recent: int
) -> tuple[Message, ...]:
    """Select recent whole groups instead of slicing through a tool exchange."""
    selected: list[tuple[Message, ...]] = []
    selected_count = 0
    for group in reversed(groups):
        selected.append(group)
        selected_count += len(group)
        if selected_count >= keep_recent:
            break
    return tuple(message for group in reversed(selected) for message in group)


def _valid_message_groups(messages: Sequence[Message]) -> tuple[tuple[Message, ...], ...]:
    """Return only model-valid groups, preserving complete tool exchanges.

    A persisted transcript can be incomplete after a process crash or from an
    older runtime.  It remains authoritative for debugging, but an incomplete
    tool exchange must not be replayed to a provider as model context.
    """
    groups: list[tuple[Message, ...]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            end = index + 1
            while end < len(messages) and isinstance(messages[end], ToolResultMessage):
                end += 1
            results = messages[index + 1 : end]
            expected = {call.id: call.name for call in message.tool_calls}
            result_ids = [result.tool_call_id for result in results]
            is_complete = (
                len(expected) == len(message.tool_calls)
                and len(results) == len(expected)
                and set(result_ids) == set(expected)
                and all(expected[result.tool_call_id] == result.tool_name for result in results)
            )
            if is_complete:
                groups.append(tuple(messages[index:end]))
            index = end
        elif isinstance(message, ToolResultMessage):
            # A tool result without its immediately preceding call is invalid
            # provider input, so retain it in the transcript but hide it here.
            index += 1
        else:
            groups.append((message,))
            index += 1
    return tuple(groups)
