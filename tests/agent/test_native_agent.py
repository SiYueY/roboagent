from __future__ import annotations
import asyncio
import unittest
from roboagent.agent import Agent, SessionBusyError
from roboagent.runtime import AssistantMessage, ModelEvent

class Model:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start")
        yield ModelEvent("text_delta", "ok")
        yield ModelEvent("done", message=AssistantMessage("ok"))

class NoisyModel:
    model_name = "test"
    async def stream(self, request, cancellation):
        yield ModelEvent("start")
        for _ in range(200):
            yield ModelEvent("text_delta", "x")
        yield ModelEvent("done", message=AssistantMessage("x" * 200))

class AgentTests(unittest.TestCase):
    def test_result_needs_no_event_consumer_and_commits_transcript(self):
        session = Agent(Model()).new_session()
        result = asyncio.run(session.run("hello"))
        self.assertEqual(result.status, "completed")
        self.assertEqual([message.role for message in session.messages], ["user", "assistant"])

    def test_events_are_independent_and_terminal_once(self):
        async def check():
            run = Agent(Model()).new_session().start("hello")
            events = [event async for event in run.events()]
            result = await run.result()
            self.assertEqual(result.status, "completed")
            self.assertEqual([event.sequence for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(sum(event.type == "agent_completed" for event in events), 1)
        asyncio.run(check())

    def test_session_rejects_second_run(self):
        async def check():
            session = Agent(Model()).new_session()
            run = session.start("one")
            with self.assertRaises(SessionBusyError): session.start("two")
            await run.result()
        asyncio.run(check())

    def test_observer_failure_does_not_fail_run(self):
        async def check():
            session = Agent(Model()).new_session()
            session.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
            self.assertEqual((await session.run("hello")).status, "completed")
        asyncio.run(check())

    def test_separate_sessions_can_run_concurrently(self):
        async def check():
            agent = Agent(Model())
            first, second = agent.new_session(), agent.new_session()
            results = await asyncio.gather(first.run("one"), second.run("two"))
            self.assertEqual([result.status for result in results], ["completed", "completed"])
        asyncio.run(check())

    def test_slow_event_stream_is_disconnected_without_blocking_run(self):
        async def check():
            run = Agent(NoisyModel()).new_session().start("hello")
            stream = run.events()
            result = await run.result()
            await stream.aclose()
            self.assertEqual(result.status, "completed")
        asyncio.run(check())
