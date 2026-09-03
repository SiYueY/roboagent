"""Durable storage for explicit, redacted public event records only."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from .event import AgentEvent
from .types import ContentSummary, Modality


class EventStore(Protocol):
    async def append(self, event: AgentEvent) -> None: ...
    async def list(self, run_id: str) -> tuple[AgentEvent, ...]: ...


class EventCodec:
    """The only persistence codec; it deliberately has no MessageContent path."""

    @staticmethod
    def encode(event: AgentEvent) -> str:
        raw: dict[str, Any] = {
            "run_id": event.run_id,
            "sequence": event.sequence,
            "type": event.type,
            "turn": event.turn,
            "content": [
                {"modality": item.modality.value, "media_type": item.media_type,
                 "source_kind": item.source_kind, "size": item.size}
                for item in event.content
            ],
            "text": event.text,
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "status": event.status,
            "error_code": event.error_code,
            "error": event.error,
            "timestamp": event.timestamp,
        }
        return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def decode(line: str) -> AgentEvent:
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("Event record must be an object.")
        content = raw.pop("content", ())
        if not isinstance(content, list):
            raise ValueError("Event content must be a list.")
        raw["content"] = tuple(
            ContentSummary(Modality(item["modality"]), item.get("media_type"), item.get("source_kind"), item.get("size"))
            for item in content
            if isinstance(item, dict)
        )
        if len(raw["content"]) != len(content):
            raise ValueError("Invalid event content summary.")
        return AgentEvent(**raw)


class MemoryEventStore:
    def __init__(self) -> None:
        self._items: dict[str, list[AgentEvent]] = {}

    async def append(self, event: AgentEvent) -> None:
        self._items.setdefault(event.run_id, []).append(event)

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]:
        return tuple(self._items.get(run_id, ()))


class JsonlEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def append(self, event: AgentEvent) -> None:
        line = EventCodec.encode(event)
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf8") as file:
                file.write(line + "\n")

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]:
        if not self.path.exists():
            return ()
        async with self._lock:
            events: list[AgentEvent] = []
            for line in self.path.read_text(encoding="utf8").splitlines():
                event = EventCodec.decode(line)
                if event.run_id == run_id:
                    events.append(event)
            return tuple(events)
