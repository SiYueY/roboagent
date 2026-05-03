"""In-memory runtime run manager."""

from __future__ import annotations

import uuid
from threading import Lock
from typing import Any

from roboagent.runtime.runs.schemas import RunRecord, RunStatus, utc_now_iso


class RunManager:
    """Thread-safe in-memory registry for runtime runs."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        thread_id: str,
        assistant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Create a pending run record."""
        record = RunRecord(
            run_id=run_id or str(uuid.uuid4()),
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.PENDING,
            metadata=metadata or {},
        )
        with self._lock:
            if record.run_id in self._runs:
                raise ValueError(f"Run '{record.run_id}' already exists.")
            self._runs[record.run_id] = record
        return record

    def get(self, run_id: str) -> RunRecord | None:
        """Return one run record by id."""
        with self._lock:
            return self._runs.get(run_id)

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> RunRecord:
        """Update a run status."""
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(f"Run '{run_id}' does not exist.")
            record.status = status
            record.updated_at = utc_now_iso()
            if error is not None:
                record.error = error
            return record

    def list_by_thread(self, thread_id: str) -> list[RunRecord]:
        """Return all runs for one thread in creation order."""
        with self._lock:
            return [run for run in self._runs.values() if run.thread_id == thread_id]


__all__ = ["RunManager"]
