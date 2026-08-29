from __future__ import annotations
import unittest
from roboagent.runtime import AgentEndEvent, AgentRunResult, MemoryRunEventStore, RunJournalSubscriber, RunManager, RunStatus

class JournalTests(unittest.TestCase):
    def test_terminal_status_is_persisted(self):
        store, manager = MemoryRunEventStore(), RunManager(); manager.create(thread_id="t", run_id="r")
        subscriber = RunJournalSubscriber(thread_id="t", run_id="r", event_store=store, run_manager=manager)
        subscriber(AgentEndEvent(AgentRunResult((), None, "max_turns", "limit", "r")))
        self.assertEqual(manager.get("r").status, RunStatus.MAX_TURNS)

if __name__ == "__main__": unittest.main()
