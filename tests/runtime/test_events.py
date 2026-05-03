from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from roboagent.runtime import JsonlRunEventStore, MemoryRunEventStore


class MemoryRunEventStoreTests(unittest.TestCase):
    def test_put_assigns_thread_local_sequence(self) -> None:
        store = MemoryRunEventStore()

        first = store.put(thread_id="t1", run_id="r1", event_type="start", category="trace")
        second = store.put(thread_id="t1", run_id="r1", event_type="end", category="trace")
        other = store.put(thread_id="t2", run_id="r2", event_type="start", category="trace")

        self.assertEqual(first.seq, 1)
        self.assertEqual(second.seq, 2)
        self.assertEqual(other.seq, 1)

    def test_list_events_filters_run_and_event_type(self) -> None:
        store = MemoryRunEventStore()
        store.put(thread_id="t1", run_id="r1", event_type="model_start", category="trace")
        store.put(thread_id="t1", run_id="r1", event_type="model_end", category="trace")
        store.put(thread_id="t1", run_id="r2", event_type="model_start", category="trace")

        events = store.list_events("t1", "r1", event_types=["model_start"])

        self.assertEqual([event.event_type for event in events], ["model_start"])

    def test_list_messages_returns_message_category(self) -> None:
        store = MemoryRunEventStore()
        store.put(thread_id="t1", run_id="r1", event_type="trace", category="trace")
        store.put(thread_id="t1", run_id="r1", event_type="message", category="message")

        messages = store.list_messages("t1")

        self.assertEqual([event.category for event in messages], ["message"])


class JsonlRunEventStoreTests(unittest.TestCase):
    def test_jsonl_store_persists_and_reloads_events(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            store = JsonlRunEventStore(path)
            store.put(
                thread_id="t1",
                run_id="r1",
                event_type="model_start",
                category="trace",
                metadata={"model": "test"},
            )
            reloaded = JsonlRunEventStore(path)
            second = reloaded.put(thread_id="t1", run_id="r1", event_type="model_end", category="trace")

            events = reloaded.list_events("t1", "r1")

        self.assertEqual(second.seq, 2)
        self.assertEqual([event.event_type for event in events], ["model_start", "model_end"])
        self.assertEqual(events[0].metadata, {"model": "test"})

    def test_jsonl_store_lists_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = JsonlRunEventStore(Path(tmpdir) / "events.jsonl")
            store.put(thread_id="t1", run_id="r1", event_type="trace", category="trace")
            store.put(thread_id="t1", run_id="r1", event_type="message", category="message")

            messages = store.list_messages("t1")

        self.assertEqual([event.category for event in messages], ["message"])


if __name__ == "__main__":
    unittest.main()
