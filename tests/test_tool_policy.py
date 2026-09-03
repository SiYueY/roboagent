"""Policy and bounded-parallel tool execution semantics."""
from __future__ import annotations

import asyncio
import unittest

from roboagent.agent import (BeforeToolAction, ToolErrorAction, ToolExecutionConfig,
    ToolExecutionMode, ToolExecutionPolicy, ToolExecutor)
from roboagent.message import TextContent, ToolCall, ToolCallStatus
from roboagent.runtime import ModelContext, RunContext, RuntimeCancellation
from roboagent.tool import Tool, ToolOutput


class _StopAfterError(ToolExecutionPolicy):
    def on_error(self, outcome):
        return ToolErrorAction.STOP_BATCH


class _FailRunAfterError(ToolExecutionPolicy):
    def on_error(self, outcome):
        return ToolErrorAction.FAIL_RUN


class _DenyFirst(ToolExecutionPolicy):
    def before_call(self, call):
        return BeforeToolAction.FAIL_RUN if call.id == "first" else BeforeToolAction.ALLOW


class ToolPolicyTests(unittest.IsolatedAsyncioTestCase):
    def _context(self):
        token = RuntimeCancellation()
        return RunContext("session", "run", token), ModelContext(None, (), ())

    def _calls(self):
        return (ToolCall("first", "tool", "{}", {}), ToolCall("second", "tool", "{}", {}))

    async def test_parallel_stop_batch_skips_pending_without_cancelling_running(self):
        second_started = asyncio.Event()

        async def handler(_, invocation):
            if invocation.call.id == "first":
                return ToolOutput((), is_error=True, error_code="bad")
            second_started.set()
            await asyncio.sleep(0)
            return ToolOutput((TextContent("ok"),))

        context, model_context = self._context()
        result = await ToolExecutor(
            {"tool": Tool("tool", "", {}, handler)}, object(),
            ToolExecutionConfig(ToolExecutionMode.PARALLEL, max_concurrency=1),
            _StopAfterError(),
        ).execute(self._calls(), context, model_context)
        self.assertEqual([outcome.status for outcome in result.outcomes], [ToolCallStatus.FAILED, ToolCallStatus.SKIPPED])
        self.assertFalse(second_started.is_set())
        self.assertFalse(result.fail_run)

    async def test_parallel_fail_run_cancels_running_and_skips_pending(self):
        started = asyncio.Event()

        async def handler(_, invocation):
            if invocation.call.id == "first":
                await started.wait()
                return ToolOutput((), is_error=True, error_code="bad")
            started.set()
            await asyncio.sleep(30)
            return ToolOutput((TextContent("late"),))

        calls = self._calls() + (ToolCall("third", "tool", "{}", {}),)
        context, model_context = self._context()
        result = await asyncio.wait_for(ToolExecutor(
            {"tool": Tool("tool", "", {}, handler)}, object(),
            ToolExecutionConfig(ToolExecutionMode.PARALLEL, max_concurrency=2),
            _FailRunAfterError(),
        ).execute(calls, context, model_context), 1)
        self.assertEqual([outcome.status for outcome in result.outcomes], [ToolCallStatus.FAILED, ToolCallStatus.CANCELLED, ToolCallStatus.SKIPPED])
        self.assertTrue(result.fail_run)

    async def test_before_fail_run_prevents_pending_calls(self):
        called = False

        async def handler(_, invocation):
            nonlocal called
            called = True
            return ToolOutput((TextContent("never"),))

        context, model_context = self._context()
        result = await ToolExecutor(
            {"tool": Tool("tool", "", {}, handler)}, object(),
            ToolExecutionConfig(ToolExecutionMode.PARALLEL, max_concurrency=1),
            _DenyFirst(),
        ).execute(self._calls(), context, model_context)
        self.assertEqual([outcome.status for outcome in result.outcomes], [ToolCallStatus.FAILED, ToolCallStatus.SKIPPED])
        self.assertTrue(result.fail_run)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
