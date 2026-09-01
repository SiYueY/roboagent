from __future__ import annotations

import asyncio
import unittest

from roboagent.context import FullContextManager, WindowContextManager
from roboagent.runtime import AssistantMessage, ToolCall, ToolDefinition, ToolResultMessage, UserMessage


class Token:
    cancelled = False
    reason = None


TOOLS = (ToolDefinition("move", "Move the robot", {}),)


class ContextManagerTests(unittest.TestCase):
    def test_full_manager_preserves_all_model_inputs(self):
        messages = (UserMessage("one"), AssistantMessage("two"))

        context = asyncio.run(
            FullContextManager().prepare(
                system_prompt="system", messages=messages, tools=TOOLS, cancellation=Token()
            )
        )

        self.assertEqual(context.system_prompt, "system")
        self.assertEqual(context.messages, messages)
        self.assertEqual(context.tools, TOOLS)

    def test_window_keeps_short_history_unchanged(self):
        messages = (UserMessage("one"), AssistantMessage("two"))
        context = asyncio.run(
            WindowContextManager(max_messages=2).prepare(
                system_prompt=None, messages=messages, tools=(), cancellation=Token()
            )
        )

        self.assertEqual(context.messages, messages)

    def test_window_never_splits_a_tool_exchange(self):
        call = ToolCall("call-1", "move", arguments={})
        exchange = (
            AssistantMessage("calling", tool_calls=(call,)),
            ToolResultMessage("call-1", "move", "done"),
        )
        messages = (UserMessage("old"), *exchange, UserMessage("latest"))

        context = asyncio.run(
            WindowContextManager(max_messages=2).prepare(
                system_prompt=None, messages=messages, tools=TOOLS, cancellation=Token()
            )
        )

        self.assertEqual(context.messages, (messages[-1],))
        self.assertEqual(messages[0].content, "old")

    def test_window_keeps_multiple_tool_results_with_their_call(self):
        calls = (ToolCall("a", "move", arguments={}), ToolCall("b", "move", arguments={}))
        exchange = (
            AssistantMessage(tool_calls=calls),
            ToolResultMessage("a", "move", "first"),
            ToolResultMessage("b", "move", "second"),
        )
        messages = (UserMessage("old"), *exchange, UserMessage("latest"))

        context = asyncio.run(
            WindowContextManager(max_messages=2).prepare(
                system_prompt=None, messages=messages, tools=TOOLS, cancellation=Token()
            )
        )

        self.assertEqual(context.messages, (messages[-1],))

    def test_window_keeps_an_oversized_newest_tool_exchange(self):
        call = ToolCall("call-1", "move", arguments={})
        exchange = (
            AssistantMessage(tool_calls=(call,)),
            ToolResultMessage("call-1", "move", "done"),
        )

        context = asyncio.run(
            WindowContextManager(max_messages=1).prepare(
                system_prompt=None, messages=exchange, tools=TOOLS, cancellation=Token()
            )
        )

        self.assertEqual(context.messages, exchange)

    def test_window_rejects_mismatched_tool_result(self):
        messages = (
            AssistantMessage(tool_calls=(ToolCall("call-1", "move", arguments={}),)),
            ToolResultMessage("other", "move", "done"),
            UserMessage("latest"),
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            asyncio.run(
                WindowContextManager(max_messages=1).prepare(
                    system_prompt=None, messages=messages, tools=TOOLS, cancellation=Token()
                )
            )

    def test_invalid_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            WindowContextManager(max_messages=0)
