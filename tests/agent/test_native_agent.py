from __future__ import annotations
import asyncio
import unittest
from roboagent.agent import Agent, AgentAlreadyRunningError
from roboagent.runtime import AssistantMessage, ModelEvent

class Token:
    cancelled = False
class Model:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start"); yield ModelEvent("text_delta", "ok"); yield ModelEvent("done", message=AssistantMessage("ok"))

class AgentTests(unittest.TestCase):
    def test_run_commits_transcript_and_events(self):
        agent = Agent(Model()); events = []
        agent.subscribe(events.append)
        result = asyncio.run(agent.run("hello"))
        self.assertEqual(result.status, "completed")
        self.assertEqual([message.role for message in agent.messages], ["user", "assistant"])
        self.assertEqual(events[-1].type, "agent_end")

    def test_observer_failure_is_isolated(self):
        agent = Agent(Model()); agent.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("journal offline")))
        self.assertEqual(asyncio.run(agent.run("hello")).status, "completed")

if __name__ == "__main__": unittest.main()
