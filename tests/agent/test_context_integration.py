from __future__ import annotations

import asyncio
import unittest

from roboagent.agent import Agent, AgentHooks
from roboagent.context import FullContextManager, WindowContextManager
from roboagent.runtime import AssistantMessage, ModelContext, ModelEvent, UserMessage


class Model:
    model_name = "test"

    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request, cancellation):
        self.requests.append(request)
        yield ModelEvent("start")
        yield ModelEvent("text_delta", "done")
        yield ModelEvent("done", message=AssistantMessage("done"))


class InvalidManager:
    async def prepare(self, **_kwargs):
        return ()


class ContextIntegrationTests(unittest.TestCase):
    def test_agent_defaults_to_full_context_manager(self):
        model = Model()
        original = UserMessage("earlier")
        session = Agent(model).new_session((original,))

        result = asyncio.run(session.run("latest"))

        self.assertEqual(result.status, "completed")
        self.assertIsInstance(session.agent.context_manager, FullContextManager)
        self.assertEqual([message.content for message in model.requests[0].context.messages], ["earlier", "latest"])

    def test_window_context_does_not_modify_session_transcript(self):
        model = Model()
        history = (UserMessage("old"), AssistantMessage("previous"), UserMessage("recent"))
        session = Agent(model, context_manager=WindowContextManager(max_messages=2)).new_session(history)

        asyncio.run(session.run("latest"))

        self.assertEqual([message.content for message in session.messages], ["old", "previous", "recent", "latest", "done"])
        self.assertEqual([message.content for message in model.requests[0].context.messages], ["recent", "latest"])

    def test_context_transform_runs_after_context_manager(self):
        model = Model()

        def transform(context, _cancellation):
            return ModelContext("changed", context.messages, context.tools)

        asyncio.run(
            Agent(model, hooks=AgentHooks(context_transforms=(transform,))).new_session().run("latest")
        )

        self.assertEqual(model.requests[0].context.system_prompt, "changed")

    def test_invalid_context_manager_result_fails_before_model_invocation(self):
        model = Model()

        result = asyncio.run(Agent(model, context_manager=InvalidManager()).new_session().run("latest"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(model.requests, [])
