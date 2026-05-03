"""JSONL-backed run event store."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from roboagent.runtime.events.store.base import RunEvent, RunEventStore


class JsonlRunEventStore(RunEventStore):
    """Append-only JSONL event store for local durable traces."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._seq_by_thread = self._load_existing_sequences()

    def put(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        content: str | dict[str, Any] = "",
        metadata: dict[str, Any] | None = None,
    ) -> RunEvent:
        with self._lock:
            seq = self._seq_by_thread.get(thread_id, 0) + 1
            self._seq_by_thread[thread_id] = seq
            event = RunEvent(
                seq=seq,
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                category=category,
                content=content,
                metadata=metadata or {},
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            return event

    def list_events(
        self,
        thread_id: str,
        run_id: str,
        *,
        event_types: list[str] | None = None,
        limit: int = 500,
    ) -> list[RunEvent]:
        event_type_filter = set(event_types or ())
        events = [
            event
            for event in self._read_events()
            if event.thread_id == thread_id
            and event.run_id == run_id
            and (not event_type_filter or event.event_type in event_type_filter)
        ]
        return events[:limit]

    def list_messages(self, thread_id: str, *, limit: int = 50) -> list[RunEvent]:
        messages = [
            event
            for event in self._read_events()
            if event.thread_id == thread_id and event.category == "message"
        ]
        return messages[-limit:]

    def _read_events(self) -> list[RunEvent]:
        if not self.path.exists():
            return []

        events: list[RunEvent] = []
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    events.append(_event_from_dict(json.loads(stripped)))
        return events

    def _load_existing_sequences(self) -> dict[str, int]:
        seq_by_thread: dict[str, int] = {}
        if not self.path.exists():
            return seq_by_thread

        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                thread_id = str(payload["thread_id"])
                seq_by_thread[thread_id] = max(seq_by_thread.get(thread_id, 0), int(payload["seq"]))
        return seq_by_thread


def _event_from_dict(payload: dict[str, Any]) -> RunEvent:
    return RunEvent(
        seq=int(payload["seq"]),
        thread_id=str(payload["thread_id"]),
        run_id=str(payload["run_id"]),
        event_type=str(payload["event_type"]),
        category=str(payload["category"]),
        content=payload.get("content", ""),
        metadata=dict(payload.get("metadata") or {}),
        created_at=str(payload["created_at"]),
    )


__all__ = ["JsonlRunEventStore"]
