"""Run event storage interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One recorded runtime event."""

    seq: int
    thread_id: str
    run_id: str
    event_type: str
    category: str
    content: str | dict[str, Any] = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event into a plain dictionary."""
        return {
            "seq": self.seq,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "category": self.category,
            "content": self.content,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


class RunEventStore(ABC):
    """Storage interface for runtime event streams."""

    @abstractmethod
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
        """Write and return one event."""

    @abstractmethod
    def list_events(
        self,
        thread_id: str,
        run_id: str,
        *,
        event_types: list[str] | None = None,
        limit: int = 500,
    ) -> list[RunEvent]:
        """Return events for a run in ascending sequence order."""

    @abstractmethod
    def list_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
    ) -> list[RunEvent]:
        """Return displayable message events for a thread."""


__all__ = ["RunEvent", "RunEventStore"]
