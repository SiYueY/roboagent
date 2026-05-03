from __future__ import annotations

import unittest

from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage

from roboagent.middleware import RunJournalMiddleware
from roboagent.runtime import MemoryRunEventStore


class RunJournalMiddlewareTests(unittest.TestCase):
    def test_agent_hooks_record_start_and_end(self) -> None:
        store = MemoryRunEventStore()
        middleware = RunJournalMiddleware(thread_id="thread-1", run_id="run-1", event_store=store)

        middleware.before_agent({}, None)
        middleware.after_agent({}, None)

        self.assertEqual(
            [event.event_type for event in store.list_events("thread-1", "run-1")],
            ["agent_start", "agent_end"],
        )

    def test_wrap_model_call_records_success(self) -> None:
        store = MemoryRunEventStore()
        middleware = RunJournalMiddleware(thread_id="thread-1", run_id="run-1", event_store=store)
        request = ModelRequest(model=object(), messages=[])

        def handler(_: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[AIMessage(content="ok")])

        middleware.wrap_model_call(request, handler)

        self.assertEqual(
            [event.event_type for event in store.list_events("thread-1", "run-1")],
            ["model_start", "model_end"],
        )

    def test_wrap_tool_call_records_error_status_from_tool_message(self) -> None:
        store = MemoryRunEventStore()
        middleware = RunJournalMiddleware(thread_id="thread-1", run_id="run-1", event_store=store)
        request = ToolCallRequest(
            tool_call={"name": "map.read", "args": {}, "id": "call-1", "type": "tool_call"},
            tool=None,
            state={},
            runtime=None,
        )

        def handler(_: ToolCallRequest) -> ToolMessage:
            return ToolMessage(content="failed", tool_call_id="call-1", status="error")

        middleware.wrap_tool_call(request, handler)
        events = store.list_events("thread-1", "run-1")

        self.assertEqual([event.event_type for event in events], ["tool_start", "tool_end"])
        self.assertEqual(events[-1].metadata["status"], "error")


if __name__ == "__main__":
    unittest.main()
