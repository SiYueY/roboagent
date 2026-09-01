from __future__ import annotations

import asyncio
import unittest

from roboagent.context import DefaultContextManager, SessionContextState
from roboagent.runtime import AssistantMessage, ToolCall, ToolResultMessage, UserMessage


class Token:
    cancelled = False
    reason = None


class ContextManagerTests(unittest.TestCase):
    def test_empty_and_single_message_contexts_are_unchanged(self):
        manager = DefaultContextManager(max_messages=2, keep_recent=1)

        empty = asyncio.run(manager.prepare((), SessionContextState(), Token()))
        one_message = UserMessage("one")
        single = asyncio.run(manager.prepare((one_message,), SessionContextState(), Token()))

        self.assertEqual(empty.context.messages, ())
        self.assertEqual(single.context.messages, (one_message,))

    def test_short_history_is_unchanged_and_state_is_preserved(self):
        messages = (UserMessage("one"), AssistantMessage("two"))
        state = SessionContextState("summary", 4)

        result = asyncio.run(DefaultContextManager(max_messages=2, keep_recent=1).prepare(messages, state, Token()))

        self.assertEqual(result.context.messages, messages)
        self.assertEqual(result.context.summary, "summary")
        self.assertIs(result.state, state)

    def test_window_never_splits_a_tool_exchange(self):
        call = ToolCall("call-1", "move", arguments={})
        messages = (
            UserMessage("old"),
            AssistantMessage("calling", tool_calls=(call,)),
            ToolResultMessage("call-1", "move", "done"),
            UserMessage("latest"),
        )

        result = asyncio.run(DefaultContextManager(max_messages=3, keep_recent=2).prepare(messages, SessionContextState(), Token()))

        self.assertEqual(result.context.messages, messages[1:])
        self.assertEqual(messages[0].content, "old")

    def test_multiple_tool_results_stay_with_their_call(self):
        calls = (ToolCall("a", "first", arguments={}), ToolCall("b", "second", arguments={}))
        exchange = (
            AssistantMessage(tool_calls=calls),
            ToolResultMessage("a", "first", "ok"),
            ToolResultMessage("b", "second", "ok"),
        )
        messages = (UserMessage("old"), *exchange, UserMessage("latest"))

        result = asyncio.run(DefaultContextManager(max_messages=4, keep_recent=2).prepare(messages, SessionContextState(), Token()))

        self.assertEqual(result.context.messages, (*exchange, messages[-1]))

    def test_incomplete_or_mismatched_tool_exchange_is_not_model_context(self):
        calls = (ToolCall("a", "first", arguments={}), ToolCall("b", "second", arguments={}))
        messages = (
            UserMessage("before"),
            AssistantMessage(tool_calls=calls),
            ToolResultMessage("a", "first", "ok"),
            UserMessage("after"),
            ToolResultMessage("orphan", "other", "ignore"),
        )

        result = asyncio.run(DefaultContextManager().prepare(messages, SessionContextState(), Token()))

        self.assertEqual([message.content for message in result.context.messages], ["before", "after"])

    def test_invalid_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            DefaultContextManager(max_messages=0)
        with self.assertRaises(ValueError):
            DefaultContextManager(max_messages=2, keep_recent=3)
