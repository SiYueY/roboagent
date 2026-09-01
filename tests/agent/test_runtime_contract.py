from __future__ import annotations

import asyncio
import unittest

from pydantic import BaseModel

from roboagent.agent import Agent, AgentHooks
from roboagent.runtime import AssistantMessage, ModelEvent, ToolCall, ToolExecutionResult
from roboagent.tool import Tool, ToolInvocation


class Params(BaseModel):
    value: str


class ToolModel:
    model_name = "test"
    def __init__(self):
        self._turn = 0
    async def stream(self, request, cancellation):
        self._turn += 1
        yield ModelEvent("start")
        if self._turn == 1:
            yield ModelEvent("done", message=AssistantMessage(tool_calls=(ToolCall("call-1", "first", arguments={"value": "x"}), ToolCall("call-2", "second", arguments={"value": "y"}))))
        else:
            yield ModelEvent("done", message=AssistantMessage("finished"))


class WaitingModel:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start")
        while not cancellation.cancelled:
            await asyncio.sleep(0.001)
        yield ModelEvent("cancelled", error="cancelled")


class RepeatingToolModel:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start")
        yield ModelEvent("done", message=AssistantMessage(tool_calls=(ToolCall("call", "missing", arguments={}),)))


def tool(name, handler):
    return Tool(name, name, Params, handler, "test", "test")


class RuntimeContractTests(unittest.TestCase):
    def test_first_tool_error_short_circuits_batch(self):
        calls: list[str] = []
        async def first(_params, _invocation):
            calls.append("first")
            return ToolExecutionResult("failed", is_error=True, error_code="execution_error")
        async def second(_params, _invocation):
            calls.append("second")
            return "unexpected"
        result = asyncio.run(Agent(ToolModel(), tools=(tool("first", first), tool("second", second))).new_session().run("go"))
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, ["first"])
        results = [message for message in result.messages if getattr(message, "role", None) == "tool"]
        self.assertEqual([message.tool_call_id for message in results], ["call-1", "call-2"])
        self.assertEqual(results[1].error_code, "batch_aborted")

    def test_timeout_uses_cooperative_cancellation(self):
        result = asyncio.run(Agent(WaitingModel(), run_timeout=0.01).new_session().run("wait"))
        self.assertEqual(result.status, "timed_out")

    def test_cancelled_error_preserves_timeout_reason(self):
        class CancelledModel:
            model_name = "test"
            async def stream(self, request, cancellation):
                yield ModelEvent("start")
                while not cancellation.cancelled:
                    await asyncio.sleep(0.001)
                raise asyncio.CancelledError()
        result = asyncio.run(Agent(CancelledModel(), run_timeout=0.01).new_session().run("wait"))
        self.assertEqual(result.status, "timed_out")

    def test_policy_denial_has_stable_error_code(self):
        async def deny(_invocation):
            from roboagent.agent import ToolCallDecision
            return ToolCallDecision(allow=False, reason="not approved")
        result = asyncio.run(Agent(ToolModel(), tools=(tool("first", lambda *_: "ok"),), hooks=AgentHooks(before_tool_call=deny)).new_session().run("go"))
        errors = [message for message in result.messages if getattr(message, "role", None) == "tool"]
        self.assertEqual(errors[0].error_code, "policy_denied")

    def test_unknown_tool_and_invalid_json_are_model_visible_errors(self):
        class InvalidModel:
            model_name = "test"
            def __init__(self): self.turn = 0
            async def stream(self, request, cancellation):
                self.turn += 1
                yield ModelEvent("start")
                if self.turn == 1:
                    yield ModelEvent("done", message=AssistantMessage(tool_calls=(ToolCall("bad", "first", raw_arguments="{", parse_error="bad json"),)))
                else:
                    yield ModelEvent("done", message=AssistantMessage("done"))
        result = asyncio.run(Agent(InvalidModel(), tools=(tool("first", lambda *_: "never"),)).new_session().run("go"))
        error = next(message for message in result.messages if getattr(message, "role", None) == "tool")
        self.assertEqual(error.error_code, "invalid_arguments")

    def test_max_turns_has_single_terminal_event(self):
        async def check():
            run = Agent(RepeatingToolModel(), max_turns=2).new_session().start("go")
            events = [event async for event in run.events()]
            result = await run.result()
            self.assertEqual(result.status, "max_turns")
            self.assertEqual(sum(event.type == "agent_completed" for event in events), 1)
        asyncio.run(check())

    def test_cancel_before_model_result(self):
        async def check():
            run = Agent(WaitingModel()).new_session().start("go")
            run.cancel()
            result = await run.result()
            self.assertEqual(result.status, "cancelled")
        asyncio.run(check())
