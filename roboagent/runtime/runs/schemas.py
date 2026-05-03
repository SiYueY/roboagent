"""Schemas for runtime run tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class RunStatus(StrEnum):
    """Lifecycle status for one runtime run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class RunRecord:
    """Mutable record for one runtime run."""

    run_id: str
    thread_id: str
    status: RunStatus
    assistant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run record into a plain dictionary."""
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "assistant_id": self.assistant_id,
            "status": self.status.value,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }


__all__ = ["RunRecord", "RunStatus", "utc_now_iso"]
