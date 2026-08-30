"""Best-effort persistence for canonical agent lifecycle events."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from .event import (
    AgentCompletedEvent,
    AgentEvent,
    AgentStartedEvent,
    MessageCompletedEvent,
    MessageDeltaEvent,
    MessageStartedEvent,
    RuntimeErrorEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from .types import AssistantMessage, Message, ToolCall, ToolResultMessage, Usage, UserMessage

logger = logging.getLogger(__name__)


class EventStore(Protocol):
    """Append-only persistence boundary for the canonical event union."""

    async def append(self, event: AgentEvent) -> None: ...

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]: ...


class MemoryEventStore:
    def __init__(self) -> None:
        self._events: dict[str, list[AgentEvent]] = {}

    async def append(self, event: AgentEvent) -> None:
        self._events.setdefault(event.run_id, []).append(event)

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]:
        return tuple(self._events.get(run_id, ()))


class JsonlEventStore:
    """JSONL event store that can reopen and read events written by another instance."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def append(self, event: AgentEvent) -> None:
        payload = asdict(event)
        payload["event_type"] = event.type
        line = json.dumps(payload, ensure_ascii=False, default=str)
        async with self._lock:
            await asyncio.to_thread(self._append_line, line)

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._read_events, run_id)

    def _append_line(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(f"{line}\n")

    def _read_events(self, run_id: str) -> tuple[AgentEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[AgentEvent] = []
        with self._path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("run_id") == run_id:
                    events.append(_event_from_dict(payload))
        return tuple(events)


class EventRecorder:
    """Subscriber that disables itself if persistence fails."""

    def __init__(self, store: EventStore) -> None:
        self._store = store
        self._failed = False

    async def __call__(self, event: AgentEvent) -> None:
        if self._failed:
            return
        try:
            await self._store.append(event)
        except Exception:
            self._failed = True
            logger.exception("event recorder disabled after store failure")


def _event_from_dict(payload: dict[str, Any]) -> AgentEvent:
    event_type = payload.pop("event_type")
    payload.pop("type", None)
    if "message" in payload:
        payload["message"] = _message_from_dict(payload["message"])
    if "tool_call" in payload:
        payload["tool_call"] = _tool_call_from_dict(payload["tool_call"])
    if "result" in payload:
        payload["result"] = _tool_result_from_dict(payload["result"])
    event_classes = {
        "agent_started": AgentStartedEvent,
        "agent_completed": AgentCompletedEvent,
        "turn_started": TurnStartedEvent,
        "turn_completed": TurnCompletedEvent,
        "message_started": MessageStartedEvent,
        "message_delta": MessageDeltaEvent,
        "message_completed": MessageCompletedEvent,
        "tool_started": ToolStartedEvent,
        "tool_completed": ToolCompletedEvent,
        "runtime_error": RuntimeErrorEvent,
    }
    return event_classes[event_type](**payload)


def _message_from_dict(payload: dict[str, Any]) -> Message:
    role = payload["role"]
    if role == "user":
        return UserMessage(payload["content"], payload["timestamp"])
    if role == "assistant":
        calls = tuple(_tool_call_from_dict(call) for call in payload["tool_calls"])
        usage = Usage(**payload["usage"])
        return AssistantMessage(
            payload["content"], calls, payload["finish_reason"], usage,
            payload["model"], payload["timestamp"],
        )
    return _tool_result_from_dict(payload)


def _tool_call_from_dict(payload: dict[str, Any]) -> ToolCall:
    return ToolCall(
        payload["id"],
        payload["name"],
        payload.get("raw_arguments", ""),
        payload.get("arguments"),
        payload.get("parse_error"),
    )


def _tool_result_from_dict(payload: dict[str, Any]) -> ToolResultMessage:
    return ToolResultMessage(
        payload["tool_call_id"],
        payload["tool_name"],
        payload["content"],
        payload.get("is_error", False),
        payload.get("details"),
        payload.get("error_code"),
        payload.get("timestamp", 0.0),
    )
