from __future__ import annotations
import asyncio
import unittest
from roboagent.agent import Agent, SessionBusyError
from roboagent.runtime import AssistantMessage, ModelEvent

class Token:
    cancelled = False
class Model:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start"); yield ModelEvent("text_delta", "ok"); yield ModelEvent("done", message=AssistantMessage("ok"))

class AgentTests(unittest.TestCase):
    def test_run_commits_transcript_and_events(self):
        agent = Agent(Model()); session = agent.new_session(); events = []
        session.subscribe(events.append)
        result = asyncio.run(session.run("hello"))
        self.assertEqual(result.status, "completed")
        self.assertEqual([message.role for message in session.messages], ["user", "assistant"])
        self.assertEqual(events[-1].type, "agent_end")

    def test_observer_failure_is_isolated(self):
        agent = Agent(Model()); session = agent.new_session(); session.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("journal offline")))
        self.assertEqual(asyncio.run(session.run("hello")).status, "completed")

    def test_session_rejects_second_run_and_sessions_are_isolated(self):
        async def check():
            agent = Agent(Model()); first, second = agent.new_session(), agent.new_session()
            run = first.start("one")
            with self.assertRaises(SessionBusyError): first.start("two")
            await run.result(); await second.run("three")
            self.assertEqual([message.content for message in first.messages if message.role == "user"], ["one"])
            self.assertEqual([message.content for message in second.messages if message.role == "user"], ["three"])
        asyncio.run(check())

if __name__ == "__main__": unittest.main()
