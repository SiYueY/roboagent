"""Durable storage for canonical, JSON-safe Run events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from roboagent.message import thaw_json

from .event import AgentEvent


class EventStore(Protocol):
    async def append(self, event: AgentEvent) -> None: ...

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]: ...


class EventCodec:
    @staticmethod
    def encode(event: AgentEvent) -> str:
        return json.dumps(
            {
                "run_id": event.run_id,
                "sequence": event.sequence,
                "type": event.type,
                "payload": thaw_json(event.payload),
                "timestamp": event.timestamp,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def decode(line: str) -> AgentEvent:
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("Event record must be an object.")
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
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    async def list(self, run_id: str) -> tuple[AgentEvent, ...]:
        if not self.path.exists():
            return ()
        async with self._lock:
            return tuple(
                event
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if (event := EventCodec.decode(line)).run_id == run_id
            )
