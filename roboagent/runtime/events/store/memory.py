"""In-memory run event store."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any

from roboagent.runtime.events.store.base import RunEvent, RunEventStore


class MemoryRunEventStore(RunEventStore):
    """Thread-safe in-memory event store for tests and local runtime."""

    def __init__(self) -> None:
        self._events: list[RunEvent] = []
        self._seq_by_thread: dict[str, int] = defaultdict(int)
        self._lock = Lock()

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
            self._seq_by_thread[thread_id] += 1
            event = RunEvent(
                seq=self._seq_by_thread[thread_id],
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                category=category,
                content=content,
                metadata=metadata or {},
            )
            self._events.append(event)
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
        with self._lock:
            events = [
                event
                for event in self._events
                if event.thread_id == thread_id
                and event.run_id == run_id
                and (not event_type_filter or event.event_type in event_type_filter)
            ]
        return events[:limit]

    def list_messages(self, thread_id: str, *, limit: int = 50) -> list[RunEvent]:
        with self._lock:
            messages = [
                event
                for event in self._events
                if event.thread_id == thread_id and event.category == "message"
            ]
        return messages[-limit:]


__all__ = ["MemoryRunEventStore"]
