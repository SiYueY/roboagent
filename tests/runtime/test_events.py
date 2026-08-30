from __future__ import annotations
import asyncio
import tempfile
import unittest
from pathlib import Path
from roboagent.runtime import (
    AgentStartedEvent,
    EventRecorder,
    JsonlEventStore,
    MemoryEventStore,
    ToolCall,
    ToolCompletedEvent,
    ToolResultMessage,
)

class EventStoreTests(unittest.TestCase):
    def test_memory_store_preserves_event_order(self):
        async def check():
            store = MemoryEventStore()
            await store.append(AgentStartedEvent(run_id="r", sequence=1, session_id="s"))
            await store.append(AgentStartedEvent(run_id="r", sequence=2, session_id="s"))
            self.assertEqual([event.sequence for event in await store.list("r")], [1, 2])
        asyncio.run(check())

    def test_stores_sort_events_by_sequence(self):
        async def check(store):
            await store.append(AgentStartedEvent(run_id="r", sequence=2, session_id="s"))
            await store.append(AgentStartedEvent(run_id="r", sequence=1, session_id="s"))
            self.assertEqual([event.sequence for event in await store.list("r")], [1, 2])
        asyncio.run(check(MemoryEventStore()))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(JsonlEventStore(Path(directory) / "events.jsonl")))

    def test_jsonl_store_appends_serialized_events(self):
        async def check(path: Path):
            store = JsonlEventStore(path)
            await store.append(AgentStartedEvent(run_id="r", sequence=1, session_id="s"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            asyncio.run(check(path))
            self.assertIn('"event_type": "agent_started"', path.read_text())

    def test_jsonl_store_reopens_and_deserializes(self):
        async def check(path: Path):
            original = AgentStartedEvent(run_id="r", sequence=1, session_id="s")
            await JsonlEventStore(path).append(original)
            events = await JsonlEventStore(path).list("r")
            self.assertEqual(events, (original,))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory) / "events.jsonl"))

    def test_recorder_disables_after_store_failure(self):
        class FailingStore:
            def __init__(self): self.calls = 0
            async def append(self, event): self.calls += 1; raise OSError("offline")
            async def list(self, run_id): return ()
        async def check():
            store = FailingStore()
            recorder = EventRecorder(store)
            event = AgentStartedEvent(run_id="r", sequence=1, session_id="s")
            await recorder(event)
            await recorder(event)
            self.assertEqual(store.calls, 1)
        asyncio.run(check())

    def test_jsonl_round_trips_nested_tool_event(self):
        async def check(path: Path):
            event = ToolCompletedEvent(
                run_id="r",
                sequence=2,
                turn=1,
                tool_call=ToolCall("call", "pose.read", arguments={"frame": "base"}),
                result=ToolResultMessage("call", "pose.read", "ok", details={"x": 1}),
            )
            await JsonlEventStore(path).append(event)
            self.assertEqual(await JsonlEventStore(path).list("r"), (event,))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory) / "events.jsonl"))
