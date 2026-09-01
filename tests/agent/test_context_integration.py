from __future__ import annotations

import asyncio
import unittest

from roboagent.agent import Agent
from roboagent.context import AgentContext, ContextResult, SessionContextState
from roboagent.runtime import AssistantMessage, ModelEvent, UserMessage


class Model:
    model_name = "test"

    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request, cancellation):
        self.requests.append(request)
        yield ModelEvent("start")
        yield ModelEvent("done", message=AssistantMessage("done"))


class Manager:
    async def prepare(self, messages, state, cancellation):
        next_state = SessionContextState("remembered", state.compacted_until + 1)
        return ContextResult(AgentContext((messages[-1],), "remembered"), next_state)


class CancellingManager:
    async def prepare(self, messages, state, cancellation):
        cancellation.cancel("user")
        return ContextResult(AgentContext(tuple(messages)), state)


class ContextIntegrationTests(unittest.TestCase):
    def test_loop_uses_working_context_and_commits_only_context_state(self):
        model = Model()
        original = UserMessage("earlier")
        session = Agent(model, system_prompt="system", context_manager=Manager()).new_session((original,))

        result = asyncio.run(session.run("latest"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(session.messages[0], original)
        self.assertEqual(session.context_state, SessionContextState("remembered", 1))
        request = model.requests[0]
        self.assertEqual([message.content for message in request.context.messages], ["latest"])
        self.assertEqual(request.context.system_prompt, "system\n\nPrevious session context:\nremembered")

    def test_cancellation_during_context_preparation_skips_model_invocation(self):
        model = Model()

        result = asyncio.run(Agent(model, context_manager=CancellingManager()).new_session().run("latest"))

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(model.requests, [])
