"""Dependency-free semantic tests for the V1 runtime kernel."""
from __future__ import annotations
import asyncio
import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from roboagent.agent import Agent, RunConfig, ToolExecutionConfig, ToolExecutionMode
from roboagent.message import (AssistantMessage, AudioContent, BytesSource, FileContent, ImageContent,
    ProtocolError, TextContent, ToolCall, ToolCallStatus, ToolResultMessage, TranscriptValidator, UserMessage)
from roboagent.runtime import AgentEvent, ContentCompleted, ContentSummary, JsonlEventStore, MediaResolutionError, MediaResolutionErrorCode, ModelCapabilities, ModelCompleted, ModelFailed, Modality, RunPhase, TextDelta, ToolCallDelta
from roboagent.runtime import FileSource, MediaLimits, MediaOwnership, ModelContext, ModelRequest, ResolvedMedia, RunContext, RuntimeCancellation
from roboagent.model.client import _content, _messages, _stream_chunks
from roboagent.tool import Tool, ToolExecutionResult, ToolOutput

class _Model:
    model_name = "test"
    capabilities = ModelCapabilities(frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), True)
    def __init__(self, replies): self.replies = iter(replies)
    async def stream(self, request, cancellation):
        reply = next(self.replies)
        if isinstance(reply, str): yield TextDelta(reply); reply = AssistantMessage((TextContent(reply),))
        yield ModelCompleted(reply)

class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_captures_media_limits_at_creation(self):
        capabilities = ModelCapabilities(
            frozenset({Modality.TEXT, Modality.IMAGE}), frozenset({Modality.TEXT}),
            frozenset({Modality.TEXT, Modality.IMAGE}), False,
        )
        original_limits = MediaLimits(max_inline_bytes=2)
        original_model = _Model(("done",)); original_model.capabilities = capabilities
        session = Agent(original_model, media_limits=original_limits).new_session()
        replacement_model = _Model(("done",)); replacement_model.capabilities = capabilities
        session.agent = Agent(replacement_model, media_limits=MediaLimits(max_inline_bytes=1))

        result = await session.run(UserMessage((ImageContent(BytesSource(b"ok"), "image/png"),), limits=original_limits))

        self.assertEqual(result.status.value, "completed")
        self.assertIs(session._media_limits, original_limits)
        TranscriptValidator(original_limits).validate(session.messages)

    async def test_failed_eager_start_rolls_back_session_state(self):
        session = Agent(_Model(("done",))).new_session()

        with patch("roboagent.agent.run.AgentRun.start_eager", side_effect=RuntimeError("loop unavailable")):
            with self.assertRaisesRegex(RuntimeError, "loop unavailable"):
                session.start("go")

        self.assertEqual(session.messages, ())
        self.assertIsNone(session._active)

    async def test_failed_eager_continuation_releases_session_ownership(self):
        session = Agent(_Model(("done",))).new_session((UserMessage("previous"),))
        original_messages = session.messages

        with patch("roboagent.agent.run.AgentRun.start_eager", side_effect=RuntimeError("loop unavailable")):
            with self.assertRaisesRegex(RuntimeError, "loop unavailable"):
                session.continue_run()

        self.assertEqual(session.messages, original_messages)
        self.assertIsNone(session._active)

    async def test_multi_tool_exchange_is_complete_and_ordered(self):
        calls = (ToolCall("a", "tool", "{}", {}), ToolCall("b", "tool", "{}", {}))
        model = _Model((AssistantMessage((), calls), "done"))
        async def handler(_, invocation): return ToolOutput((TextContent(invocation.call.id),))
        session = Agent(model, tools=(Tool("tool", "", {}, handler),)).new_session()
        result = await session.run("go", config=RunConfig(tool_execution=ToolExecutionConfig(ToolExecutionMode.PARALLEL)))
        self.assertEqual(result.status.value, "completed")
        self.assertEqual([message.tool_call_id for message in session.messages if isinstance(message, ToolResultMessage)], ["a", "b"])
        TranscriptValidator(session.agent.media_limits).validate(session.messages)

    async def test_invalid_tool_output_is_a_single_failed_outcome(self):
        calls = (ToolCall("a", "tool", "{}", {}),)
        model = _Model((AssistantMessage((), calls), "done"))
        async def handler(_, invocation): return {"not": "tool output"}
        session = Agent(model, tools=(Tool("tool", "", {}, handler),)).new_session()
        result = await session.run("go")
        tool = next(message for message in session.messages if isinstance(message, ToolResultMessage))
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(tool.status, ToolCallStatus.FAILED)
        self.assertEqual(tool.error.code, "invalid_tool_output")

    async def test_parallel_tool_events_follow_completion_but_transcript_follows_call_order(self):
        calls = (ToolCall("a", "tool", "{}", {}), ToolCall("b", "tool", "{}", {}))
        model = _Model((AssistantMessage((), calls), "done"))
        async def handler(_, invocation):
            if invocation.call.id == "a": await asyncio.sleep(0.02)
            return ToolOutput((TextContent(invocation.call.id),))
        session = Agent(model, tools=(Tool("tool", "", {}, handler),)).new_session()
        run = session.start("go", config=RunConfig(tool_execution=ToolExecutionConfig(ToolExecutionMode.PARALLEL)))
        await run.result()
        events = [event async for event in run.events() if event.type == "tool_completed"]
        transcript = [message.tool_call_id for message in session.messages if isinstance(message, ToolResultMessage)]
        self.assertEqual([event.tool_call_id for event in events], ["b", "a"])
        self.assertEqual(transcript, ["a", "b"])

    async def test_events_replay_and_are_media_safe(self):
        session = Agent(_Model(("done",))).new_session()
        run = session.start("go")
        await run.result()
        events = [event async for event in run.events()]
        self.assertEqual(events[-1].type, "run_completed")
        self.assertTrue(all(not hasattr(event, "message") for event in events))

    async def test_jsonl_event_store_uses_redacted_event_codec(self):
        with TemporaryDirectory() as directory:
            store = JsonlEventStore(Path(directory) / "events.jsonl")
            event = AgentEvent("run", 1, "model_completed", content=(ContentSummary(Modality.IMAGE, "image/png", "bytes", 5),))
            await store.append(event)
            self.assertEqual(await store.list("run"), (event,))
            raw = (Path(directory) / "events.jsonl").read_text(encoding="utf8")
            self.assertNotIn("BytesSource", raw)
            self.assertNotIn("data", raw)

    async def test_slow_event_subscriber_is_disconnected_and_finishes_iteration(self):
        class Noisy(_Model):
            async def stream(self, request, cancellation):
                for _ in range(200): yield TextDelta("x")
                yield ModelCompleted(AssistantMessage())
        run = Agent(Noisy(())).new_session().start("go")
        stream = run.events()  # register before the producer emits its backlog
        await run.result()
        received = [event async for event in stream]
        self.assertLessEqual(len(received), 128)

    async def test_terminal_event_replay_keeps_history_larger_than_live_queue(self):
        class Noisy(_Model):
            async def stream(self, request, cancellation):
                for _ in range(200):
                    yield TextDelta("x")
                yield ModelCompleted(AssistantMessage())
        run = Agent(Noisy(())).new_session().start("go")
        await run.result()
        events = [event async for event in run.events()]
        self.assertGreater(len(events), 128)
        self.assertEqual(events[-1].type, "run_completed")

    async def test_public_run_state_tracks_phase_without_raw_content(self):
        started = asyncio.Event()
        release = asyncio.Event()
        class Blocking(_Model):
            async def stream(self, request, cancellation):
                started.set()
                await release.wait()
                yield TextDelta("done")
                yield ModelCompleted(AssistantMessage((TextContent("done"),)))
        run = Agent(Blocking(())).new_session().start("go")
        await started.wait()
        self.assertEqual(run.state.phase, RunPhase.MODEL)
        self.assertEqual(run.state.streaming_content, ())
        release.set()
        await run.result()
        self.assertEqual(run.state.phase, RunPhase.TERMINAL)

    async def test_invalid_incremental_tool_call_is_model_error_without_assistant_commit(self):
        class Invalid(_Model):
            async def stream(self, request, cancellation):
                yield ToolCallDelta(0, name="tool", arguments_delta="{}")
                yield ModelCompleted(AssistantMessage())
        session = Agent(Invalid(())).new_session()
        result = await session.run("go")
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.error.code, "incomplete_tool_call")
        self.assertEqual([message.role for message in session.messages], ["user"])

    async def test_provider_failure_code_is_preserved_without_transcript_commit(self):
        class Failing(_Model):
            async def stream(self, request, cancellation):
                yield ModelFailed("provider_authenticationerror_401")
        session = Agent(Failing(())).new_session()
        result = await session.run("go")
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.error.code, "provider_authenticationerror_401")
        self.assertEqual([message.role for message in session.messages], ["user"])

    async def test_media_resolution_failure_is_a_model_error(self):
        class Failing(_Model):
            async def stream(self, request, cancellation):
                raise MediaResolutionError(MediaResolutionErrorCode.FETCH_FAILED, "safe")
                yield  # pragma: no cover
        result = await Agent(Failing(())).new_session().run("go")
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.termination_reason.value, "model_error")
        self.assertEqual(result.error.code, "fetch_failed")

    async def test_unsupported_input_modality_fails_before_model_invocation(self):
        called = False
        class TextOnly(_Model):
            async def stream(self, request, cancellation):
                nonlocal called
                called = True
                yield ModelCompleted(AssistantMessage("never"))
        session = Agent(TextOnly(())).new_session()
        run = session.start(UserMessage((ImageContent(BytesSource(b"image"), "image/png"),), limits=session.agent.media_limits))
        result = await run.result()
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.termination_reason.value, "model_error")
        self.assertEqual(result.error.code, "unsupported_input_modality")
        self.assertIn("model_failed", [event.type async for event in run.events()])
        self.assertFalse(called)

    async def test_unsupported_output_modality_fails_before_assistant_commit(self):
        class TextOnly(_Model):
            async def stream(self, request, cancellation):
                yield ModelCompleted(AssistantMessage((ImageContent(BytesSource(b"image"), "image/png"),)))
        session = Agent(TextOnly(())).new_session()
        run = session.start("go")
        result = await run.result()
        self.assertEqual(result.error.code, "unsupported_output_modality")
        self.assertEqual([message.role for message in session.messages], ["user"])
        self.assertIn("model_failed", [event.type async for event in run.events()])

    async def test_mixed_stream_preserves_non_text_content_order(self):
        class Mixed(_Model):
            capabilities = ModelCapabilities(frozenset({Modality.TEXT}), frozenset({Modality.TEXT, Modality.IMAGE}), frozenset({Modality.TEXT}), False)
            async def stream(self, request, cancellation):
                yield TextDelta("A")
                yield ContentCompleted(ImageContent(BytesSource(b"image"), "image/png"))
                yield TextDelta("B")
                yield ModelCompleted(AssistantMessage())
        result = await Agent(Mixed(())).new_session().run("go")
        self.assertEqual([type(value) for value in result.final_message.content], [TextContent, ImageContent, TextContent])
        self.assertEqual([value.text for value in result.final_message.content if isinstance(value, TextContent)], ["A", "B"])

    async def test_openai_adapter_generates_missing_tool_call_id(self):
        class Chunks:
            def __init__(self): self.sent = False
            def __aiter__(self): return self
            async def __anext__(self):
                if self.sent: raise StopAsyncIteration
                self.sent = True
                function = SimpleNamespace(name="tool", arguments="{}")
                call = SimpleNamespace(index=0, id=None, function=function)
                delta = SimpleNamespace(content=None, tool_calls=(call,))
                return SimpleNamespace(choices=(SimpleNamespace(delta=delta),))
        items = [item async for item in _stream_chunks(Chunks(), RuntimeCancellation(), "provider")]
        delta = next(item for item in items if isinstance(item, ToolCallDelta) and item.call_id)
        completed = next(item for item in items if isinstance(item, ModelCompleted))
        self.assertEqual(delta.call_id, "provider:tool:0")
        self.assertEqual(completed.message.tool_calls[0].id, delta.call_id)

    async def test_lifecycle_hooks_are_observational_and_fail_open(self):
        calls = (ToolCall("a", "tool", "{}", {}),)
        model = _Model((AssistantMessage((), calls), "done"))
        seen = []
        class Hooks:
            def on_run_start(self, context): seen.append("run_start")
            def on_turn_start(self, context): seen.append("turn_start")
            def on_model_start(self, context): seen.append("model_start")
            def on_model_end(self, message): seen.append("model_end")
            def on_tool_start(self, call): seen.append("tool_start")
            def on_tool_end(self, outcome): seen.append(("tool_end", outcome.status.value))
            def on_turn_end(self, context): seen.append("turn_end")
            def on_run_end(self, result): seen.append("run_end"); raise RuntimeError("ignored")
        async def handler(_, invocation): return ToolOutput((TextContent("ok"),))
        with self.assertLogs("roboagent.agent.run", "WARNING") as logs:
            result = await Agent(model, tools=(Tool("tool", "", {}, handler),), hooks=Hooks()).new_session().run("go")
        self.assertEqual(result.status.value, "completed")
        self.assertIn("on_run_end failed", logs.output[0])
        self.assertEqual(seen, ["run_start", "turn_start", "model_start", "model_end", "tool_start", ("tool_end", "completed"), "turn_end", "turn_start", "model_start", "model_end", "turn_end", "run_end"])

    async def test_pending_follow_up_becomes_next_turn(self):
        class Delayed(_Model):
            async def stream(self, request, cancellation):
                yield TextDelta("first")
                await asyncio.sleep(0)
                yield ModelCompleted(AssistantMessage((TextContent(next(self.replies)),)))
        session = Agent(Delayed(("first", "second"))).new_session()
        run = session.start("go")
        await asyncio.sleep(0)
        run.follow_up("again")
        result = await run.result()
        self.assertEqual(result.turns, 2)
        self.assertEqual([message.role for message in session.messages], ["user", "assistant", "user", "assistant"])

    async def test_continue_run_creates_new_execution_without_new_user_message(self):
        session = Agent(_Model(("first", "second"))).new_session()
        first = await session.run("go")
        second = await session.continue_run().result()
        self.assertEqual((first.status.value, second.status.value), ("completed", "completed"))
        self.assertEqual([message.role for message in session.messages], ["user", "assistant", "assistant"])

    async def test_cancelled_tool_batch_still_commits_terminal_results(self):
        calls = (ToolCall("a", "slow", "{}", {}), ToolCall("b", "slow", "{}", {}))
        model = _Model((AssistantMessage((), calls),))
        started = asyncio.Event()
        async def handler(_, invocation):
            started.set()
            await asyncio.sleep(10)
            return ToolOutput((TextContent("late"),))
        session = Agent(model, tools=(Tool("slow", "", {}, handler),)).new_session()
        run = session.start("go")
        await started.wait()
        run.cancel()
        result = await asyncio.wait_for(run.result(), 1)
        self.assertEqual(result.status.value, "cancelled")
        self.assertEqual(result.turns, 1)
        tools = [message for message in session.messages if isinstance(message, ToolResultMessage)]
        self.assertEqual(tools[0].status, ToolCallStatus.CANCELLED)
        self.assertIn(tools[1].status, {ToolCallStatus.CANCELLED, ToolCallStatus.SKIPPED})
        TranscriptValidator(session.agent.media_limits).validate(session.messages)

    async def test_cancel_does_not_start_pending_bounded_parallel_tool(self):
        calls = (ToolCall("a", "slow", "{}", {}), ToolCall("b", "slow", "{}", {}))
        model = _Model((AssistantMessage((), calls),))
        started = asyncio.Event()
        async def handler(_, invocation):
            started.set()
            await asyncio.sleep(30)
            return ToolOutput((TextContent("late"),))
        session = Agent(model, tools=(Tool("slow", "", {}, handler),)).new_session()
        run = session.start("go", config=RunConfig(tool_execution=ToolExecutionConfig(ToolExecutionMode.PARALLEL, max_concurrency=1)))
        await started.wait()
        run.cancel()
        await asyncio.wait_for(run.result(), 1)
        tools = [message for message in session.messages if isinstance(message, ToolResultMessage)]
        self.assertEqual([message.status for message in tools], [ToolCallStatus.CANCELLED, ToolCallStatus.SKIPPED])

    async def test_steer_observes_tools_without_early_transcript_commit(self):
        calls = (ToolCall("a", "slow", "{}", {}), ToolCall("b", "slow", "{}", {}))
        model = _Model((AssistantMessage((), calls), "done"))
        started = asyncio.Event()
        release = asyncio.Event()
        async def handler(_, invocation):
            started.set()
            await release.wait()
            return ToolOutput((TextContent(invocation.call.id),))
        session = Agent(model, tools=(Tool("slow", "", {}, handler),)).new_session()
        run = session.start("go", config=RunConfig(tool_execution=ToolExecutionConfig(ToolExecutionMode.PARALLEL, max_concurrency=1)))
        await started.wait()
        run.steer("change direction")
        # The steer is observable by ToolExecutor but remains pending until the
        # complete tool exchange has been committed in original call order.
        self.assertEqual([message.role for message in session.messages], ["user", "assistant"])
        release.set()
        result = await asyncio.wait_for(run.result(), 1)
        self.assertEqual(result.status.value, "completed")
        tools = [message for message in session.messages if isinstance(message, ToolResultMessage)]
        self.assertEqual([message.status for message in tools], [ToolCallStatus.COMPLETED, ToolCallStatus.SKIPPED])
        self.assertEqual([message.role for message in session.messages], ["user", "assistant", "tool", "tool", "user", "assistant"])
        TranscriptValidator(session.agent.media_limits).validate(session.messages)

    async def test_steer_skips_pending_sequential_tool(self):
        calls = (ToolCall("a", "slow", "{}", {}), ToolCall("b", "slow", "{}", {}))
        model = _Model((AssistantMessage((), calls), "done"))
        started = asyncio.Event()
        release = asyncio.Event()
        async def handler(_, invocation):
            started.set()
            await release.wait()
            return ToolOutput((TextContent(invocation.call.id),))
        session = Agent(model, tools=(Tool("slow", "", {}, handler),)).new_session()
        run = session.start("go")
        await started.wait()
        run.steer("only finish current")
        release.set()
        await asyncio.wait_for(run.result(), 1)
        tools = [message for message in session.messages if isinstance(message, ToolResultMessage)]
        self.assertEqual([message.status for message in tools], [ToolCallStatus.COMPLETED, ToolCallStatus.SKIPPED])

    async def test_cancel_commits_received_pending_control_at_final_boundary(self):
        calls = (ToolCall("a", "slow", "{}", {}),)
        model = _Model((AssistantMessage((), calls),))
        started = asyncio.Event()
        async def handler(_, invocation):
            started.set()
            await asyncio.sleep(30)
            return ToolOutput((TextContent("late"),))
        session = Agent(model, tools=(Tool("slow", "", {}, handler),)).new_session()
        run = session.start("go")
        await started.wait()
        run.follow_up("keep this")
        run.cancel()
        result = await asyncio.wait_for(run.result(), 1)
        self.assertEqual(result.status.value, "cancelled")
        self.assertEqual(result.uncommitted_controls, ())
        self.assertEqual([message.role for message in session.messages], ["user", "assistant", "tool", "user"])
        self.assertIn("cancellation_requested", [event.type async for event in run.events()])
        run.cancel()  # terminal cancellation is a no-op

    async def test_timeout_preserves_timed_out_status(self):
        class Slow(_Model):
            async def stream(self, request, cancellation):
                await asyncio.sleep(1)
                yield ModelCompleted(AssistantMessage((TextContent("late"),)))
        result = await Agent(Slow(())).new_session().run("go", config=RunConfig(timeout=0.01))
        self.assertEqual(result.status.value, "timed_out")
        self.assertEqual(result.turns, 1)

    async def test_max_turns_counts_model_invocations_and_preserves_complete_tool_exchange(self):
        calls = (ToolCall("a", "tool", "{}", {}),)
        model = _Model((AssistantMessage((), calls), "would be next"))
        async def handler(_, invocation): return ToolOutput("ok")
        session = Agent(model, tools=(Tool("tool", "", {}, handler),)).new_session()
        result = await session.run("go", config=RunConfig(max_turns=1))
        self.assertEqual(result.status.value, "max_turns")
        self.assertEqual(result.turns, 1)
        TranscriptValidator(session.agent.media_limits).validate(session.messages)

    async def test_tool_model_context_is_opt_in(self):
        calls = (ToolCall("call", "tool", "{}", {}),)
        seen = []
        async def handler(_, invocation):
            seen.append(invocation.model_context)
            return ToolOutput("ok")
        model = _Model((AssistantMessage((), calls), "done"))
        await Agent(model, tools=(Tool("tool", "", {}, handler),)).new_session().run("go")
        self.assertEqual(seen, [None])

        seen.clear()
        model = _Model((AssistantMessage((), calls), "done"))
        await Agent(model, tools=(Tool("tool", "", {}, handler, expose_model_context=True),)).new_session().run("go")
        self.assertIsInstance(seen[0], ModelContext)

    def test_canonical_media_and_transcript_validation(self):
        with self.assertRaises(ProtocolError): UserMessage("  ", limits=Agent(_Model(())).media_limits)
        user = UserMessage((TextContent(""), ImageContent(BytesSource(b"x"), "image/png")), limits=Agent(_Model(())).media_limits)
        self.assertEqual(len(user.content), 2)
        with self.assertRaises(ProtocolError):
            TranscriptValidator(Agent(_Model(())).media_limits).validate((ToolResultMessage("x", "t", ToolCallStatus.COMPLETED),))
        self.assertEqual(AssistantMessage("hello").content, (TextContent("hello"),))
        self.assertEqual(ToolResultMessage("x", "t", ToolCallStatus.COMPLETED, [TextContent("ok")]).content, (TextContent("ok"),))
        self.assertEqual(ToolOutput("tool text").content, (TextContent("tool text"),))
        self.assertIs(ToolExecutionResult, ToolOutput)
        usage = {"input_tokens": 1}
        assistant = AssistantMessage("hello", usage=usage)
        usage["input_tokens"] = 2
        self.assertEqual(assistant.usage["input_tokens"], 1)
        arguments = {"nested": ["original"]}
        call = ToolCall("call", "tool", '{"nested":["original"]}', arguments)
        arguments["nested"].append("mutated")
        self.assertEqual(call.arguments["nested"], ("original",))
        with self.assertRaises(TypeError): call.arguments["new"] = "value"

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        tool = Tool("tool", "", schema, lambda _, __: "ok")
        schema["properties"]["name"]["type"] = "number"
        self.assertEqual(tool.parameters["properties"]["name"]["type"], "string")
        modalities = {Modality.TEXT}
        capabilities = ModelCapabilities(modalities, modalities, modalities)
        modalities.add(Modality.IMAGE)
        self.assertEqual(capabilities.input_modalities, frozenset({Modality.TEXT}))
        with self.assertRaises(ValueError):
            ToolOutput("bad", is_error=True, error_code="secret=token")

    async def test_resolved_media_validates_and_releases_owned_bytes(self):
        source = FileSource("/tmp/image.png")
        with self.assertRaises(ValueError):
            ResolvedMedia(b"x", "image/png", -1, source, MediaOwnership.OWNED)
        with self.assertRaises(ValueError):
            ResolvedMedia(b"x", "image/png", 2, source, MediaOwnership.OWNED)
        media = ResolvedMedia(b"x", "image/png", 1, source, MediaOwnership.OWNED)
        await media.close()
        self.assertEqual(media.payload, b"")

    async def test_external_media_uses_resolver_and_closes_resource(self):
        class Resolver:
            def __init__(self): self.request = None; self.resource = None
            async def resolve(self, source, *, expected_media_type, run_context, cancellation):
                self.request = (source, expected_media_type, run_context)
                self.resource = ResolvedMedia(b"image", "image/png", 5, source, MediaOwnership.BORROWED)
                return self.resource
        resolver = Resolver()
        token = RuntimeCancellation()
        context = RunContext("session", "run", token)
        request = ModelRequest("model", ModelContext(None, (), ()), context, resolver)
        encoded, resources = await _content((ImageContent(FileSource("/tmp/image.png"), "image/png"),), request)
        self.assertEqual(resolver.request[1], "image/png")
        self.assertEqual(resolver.request[2].run_id, "run")
        self.assertEqual(encoded[0]["type"], "image_url")
        await resources[0].close()
        self.assertTrue(resolver.resource._closed)

    async def test_external_media_resolution_is_parallel_but_content_order_is_canonical(self):
        class Resolver:
            def __init__(self): self.started = []; self.resources = []
            async def resolve(self, source, *, expected_media_type, run_context, cancellation):
                self.started.append(source.path)
                if source.path.endswith("first.png"):
                    await asyncio.sleep(0.02)
                    payload = b"first"
                else:
                    payload = b"second"
                resource = ResolvedMedia(payload, "image/png", len(payload), source, MediaOwnership.BORROWED)
                self.resources.append(resource)
                return resource
        resolver = Resolver()
        token = RuntimeCancellation()
        request = ModelRequest("model", ModelContext(None, (), ()), RunContext("session", "run", token), resolver)
        encoded, resources = await _content((
            ImageContent(FileSource("/tmp/first.png"), "image/png"),
            TextContent("between"),
            ImageContent(FileSource("/tmp/second.png"), "image/png"),
        ), request)
        self.assertEqual(resolver.started, ["/tmp/first.png", "/tmp/second.png"])
        self.assertTrue(encoded[0]["image_url"]["url"].endswith(base64.b64encode(b"first").decode()))
        self.assertEqual(encoded[1], {"type": "text", "text": "between"})
        self.assertTrue(encoded[2]["image_url"]["url"].endswith(base64.b64encode(b"second").decode()))
        for resource in resources: await resource.close()
        self.assertTrue(all(resource._closed for resource in resolver.resources))

    async def test_external_media_failure_cancels_sibling_and_closes_successes(self):
        cancelled = asyncio.Event()
        class Resolver:
            def __init__(self): self.resource = None
            async def resolve(self, source, **kwargs):
                if source.path.endswith("bad.png"):
                    await asyncio.sleep(0)
                    raise MediaResolutionError(MediaResolutionErrorCode.FETCH_FAILED, "safe")
                self.resource = ResolvedMedia(b"good", "image/png", 4, source, MediaOwnership.BORROWED)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    cancelled.set()
                    return self.resource
        resolver = Resolver()
        request = ModelRequest("model", ModelContext(None, (), ()), RunContext("session", "run", RuntimeCancellation()), resolver)
        with self.assertRaises(MediaResolutionError):
            await _content((
                ImageContent(FileSource("/tmp/bad.png"), "image/png"),
                ImageContent(FileSource("/tmp/good.png"), "image/png"),
            ), request)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(resolver.resource._closed)

    async def test_resolver_failure_is_sanitized_as_media_error(self):
        class Resolver:
            async def resolve(self, source, **kwargs): raise OSError("private filesystem details")
        token = RuntimeCancellation()
        request = ModelRequest("model", ModelContext(None, (), ()), RunContext("session", "run", token), Resolver())
        with self.assertRaises(MediaResolutionError) as caught:
            await _content((ImageContent(FileSource("/tmp/image.png"), "image/png"),), request)
        self.assertEqual(caught.exception.code, MediaResolutionErrorCode.FETCH_FAILED)

    async def test_openai_adapter_encodes_canonical_tool_exchange(self):
        call = ToolCall("call", "lookup", '{"q":"x"}', {"q": "x"})
        context = ModelContext("system", (
            UserMessage("question"),
            AssistantMessage("working", (call,)),
            ToolResultMessage("call", "lookup", ToolCallStatus.COMPLETED, "answer"),
        ), ())
        request = ModelRequest("model", context, RunContext("session", "run", RuntimeCancellation()))
        messages, resources = await _messages(request)
        self.assertEqual(resources, [])
        self.assertEqual([message["role"] for message in messages], ["system", "user", "assistant", "tool"])
        self.assertEqual(messages[2]["tool_calls"][0]["function"]["arguments"], '{"q":"x"}')
        self.assertEqual(messages[3]["tool_call_id"], "call")

if __name__ == "__main__": unittest.main()
