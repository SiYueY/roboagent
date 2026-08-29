"""Observability subscriber for native agent events."""

from __future__ import annotations

from roboagent.runtime.events import MemoryRunEventStore, RunEventStore
from roboagent.runtime.protocol import AgentEndEvent, AgentEvent
from roboagent.runtime.runs import RunManager, RunStatus


class RunJournalSubscriber:
    def __init__(self, *, thread_id: str, run_id: str, event_store: RunEventStore | None = None, run_manager: RunManager | None = None) -> None:
        self.thread_id, self.run_id = thread_id, run_id
        self.event_store, self.run_manager = event_store or MemoryRunEventStore(), run_manager
    def __call__(self, event: AgentEvent) -> None:
        self.event_store.put(thread_id=self.thread_id, run_id=self.run_id, event_type=event.type, category="trace", content=str(event))
        if isinstance(event, AgentEndEvent) and self.run_manager:
            self.run_manager.set_status(self.run_id, RunStatus(event.result.status), error=event.result.error)
