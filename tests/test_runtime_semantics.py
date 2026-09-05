from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent, Session
from roboagent.agent import HookDecision, RunConfig, SessionBusyError
from roboagent.context import MessageSegment, ModelContext, PromptInput
from roboagent.message import (
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
)
from roboagent.runtime import Modality, RunStatus
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectStatus,
    ToolExecutionMode,
    ToolJsonContent,
    ToolRegistry,
    ToolTextContent,
)
from roboagent.message import FrozenJsonObject


class Replies:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}),
        frozenset({Modality.TEXT}),
        True,
        True,
    )

    def __init__(self, replies: tuple[AssistantMessage, ...]) -> None:
        self.replies = iter(replies)

    async def stream(self, context, settings=None):
        message = next(self.replies)
        sequence = 0
        yield ResponseStarted("response", sequence)
        sequence += 1
        for call_index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, call_index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, call_index, call)
            sequence += 1
        for content in message.content:
            if isinstance(content, TextContent):
                yield TextDelta(sequence, content.text)
                sequence += 1
        reason = FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP
        yield ResponseCompleted(sequence, ModelResponse(message, reason))


def test_top_level_api_and_single_active_run() -> None:
    asyncio.run(_top_level_api_and_single_active_run())


async def _top_level_api_and_single_active_run() -> None:
    assert Session.__name__ == "Session"
    release = asyncio.Event()

    class Blocking(Replies):
        async def stream(self, context, settings=None):
            yield ResponseStarted("response")
            await release.wait()
            message = AssistantMessage("done")
            yield TextDelta(1, "done")
            yield ResponseCompleted(2, ModelResponse(message, FinishReason.STOP))

    session = Agent(Blocking(())).new_session()
    run = session.start(UserMessage("first"))
    with pytest.raises(SessionBusyError):
        session.start(UserMessage("second"))
    release.set()
    assert (await run.result()).status is RunStatus.COMPLETED
    assert session.active_run_id is None


def test_pending_input_queues_and_consumes_at_next_boundary() -> None:
    asyncio.run(_pending_input_queues_and_consumes_at_next_boundary())


async def _pending_input_queues_and_consumes_at_next_boundary() -> None:
    model = Replies((AssistantMessage("first"), AssistantMessage("second")))
    session = Agent(model).new_session()
    receipt = await session.follow_up(UserMessage("queued"))
    assert receipt.sequence == 1
    assert session.messages == ()
    result = await session.run()
    assert result.status is RunStatus.COMPLETED
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_tool_exchange_is_atomic_and_effect_is_committed() -> None:
    asyncio.run(_tool_exchange_is_atomic_and_effect_is_committed())


async def _tool_exchange_is_atomic_and_effect_is_committed() -> None:
    call = ToolCall("call", "lookup", FrozenJsonObject({"value": 1}))
    model = Replies((AssistantMessage(tool_calls=(call,)), AssistantMessage("done")))

    async def handler(arguments, context):
        return ToolJsonContent(arguments)

    registry = ToolRegistry(
        (
            Tool(
                ToolDefinition(
                    "lookup",
                    "Return the provided value.",
                    FrozenJsonObject(
                        {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        }
                    ),
                ),
                handler,
                ToolExecutionMode.CONCURRENT,
                ToolEffectKind.READ_ONLY,
            ),
        )
    )
    session = Agent(model, tool_registry=registry, prompt=PromptInput("Help.")) .new_session()
    run = session.start(UserMessage("go"))
    result = await run.result()
    assert result.status is RunStatus.COMPLETED
    assert len(result.effects) == 1 and result.effects[0].transcript_committed
    assert isinstance(session.messages[2], ToolResultMessage)
    events = [event async for event in run.subscribe()]
    assert events[0].type == "run.started"
    assert events[-1].type == "run.completed"
    assert [event.type for event in events].count("tool_batch.committed") == 1


def test_after_model_failure_does_not_commit_response() -> None:
    asyncio.run(_after_model_failure_does_not_commit_response())


async def _after_model_failure_does_not_commit_response() -> None:
    class Hook:
        async def after_model(self, context, response):
            raise RuntimeError("stop")

    session = Agent(Replies((AssistantMessage("not committed"),)), hooks=(Hook(),)).new_session()
    result = await session.run(UserMessage("go"))
    assert result.status is RunStatus.FAILED
    assert [message.role for message in session.messages] == ["user"]
    assert result.output is None


def test_before_model_decision_fails_without_invocation() -> None:
    asyncio.run(_before_model_decision_fails_without_invocation())


async def _before_model_decision_fails_without_invocation() -> None:
    class Never(Replies):
        called = False

        async def stream(self, context, settings=None):
            self.called = True
            yield  # pragma: no cover

    class Hook:
        async def before_model(self, context):
            return HookDecision.FAIL_RUN

    model = Never(())
    result = await Agent(model, hooks=(Hook(),)).new_session().run(UserMessage("go"))
    assert result.status is RunStatus.FAILED
    assert not model.called


def test_model_cancellation_closes_stream_and_timeout_is_failed() -> None:
    async def check() -> None:
        class Blocking:
            capabilities = Replies.capabilities

            def __init__(self) -> None:
                self.closed = False

            async def stream(self, context, settings=None):
                try:
                    yield ResponseStarted("response", 0)
                    await asyncio.Event().wait()
                finally:
                    self.closed = True

        cancelled_model = Blocking()
        run = Agent(cancelled_model).new_session().start(UserMessage("go"))
        await asyncio.sleep(0)
        run.cancel()
        result = await run.result()
        assert result.status is RunStatus.CANCELLED and cancelled_model.closed

        timeout_model = Blocking()
        result = await Agent(timeout_model).new_session().run(UserMessage("go"), config=RunConfig(timeout=0.01))
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "timeout"
        assert timeout_model.closed

    asyncio.run(check())


def test_cleanup_failure_retains_committed_output_and_terminal_is_last() -> None:
    async def check() -> None:
        provisional = []

        class Hook:
            async def on_run_end(self, context):
                provisional.append(context.provisional_status)
                raise RuntimeError("cleanup")

        session = Agent(Replies((AssistantMessage("committed"),)), hooks=(Hook(),)).new_session()
        run = session.start(UserMessage("go"))
        result = await run.result()
        events = [event async for event in run.subscribe()]
        assert provisional == [RunStatus.COMPLETED]
        assert result.status is RunStatus.FAILED and result.output == session.messages[-1]
        assert result.error is not None and result.error.code == "cleanup_hook_error"
        assert events[-1].type == "run.failed"

    asyncio.run(check())


def test_cancelled_side_effect_is_retained_and_exchange_not_committed() -> None:
    async def check() -> None:
        call = ToolCall("call", "act", FrozenJsonObject())
        started = asyncio.Event()

        async def handler(arguments, context):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ToolTextContent("effect happened")

        registry = ToolRegistry((Tool(ToolDefinition("act", "Act.", FrozenJsonObject({"type": "object"})), handler, ToolExecutionMode.SERIAL, ToolEffectKind.SIDE_EFFECTING),))
        session = Agent(Replies((AssistantMessage(tool_calls=(call,)),)), tool_registry=registry).new_session()
        run = session.start(UserMessage("go"))
        await started.wait()
        run.cancel()
        result = await run.result()
        assert result.status is RunStatus.CANCELLED
        assert len(result.effects) == 1 and not result.effects[0].transcript_committed
        assert result.effects[0].status is ToolEffectStatus.SUCCEEDED
        assert result.retry_safe is False
        assert [message.role for message in session.messages] == ["user"]

    asyncio.run(check())


def test_input_enqueued_during_cleanup_remains_for_next_run() -> None:
    async def check() -> None:
        session = None
        ended = 0

        class Hook:
            async def on_run_end(self, context):
                nonlocal ended
                assert session is not None
                if ended == 0:
                    await session.follow_up(UserMessage("older"))
                ended += 1

        session = Agent(
            Replies((AssistantMessage("first done"), AssistantMessage("second done"))),
            hooks=(Hook(),),
        ).new_session()
        result = await session.run(UserMessage("first"))
        assert result.status is RunStatus.COMPLETED
        pending = await session.pending_inputs()
        assert len(pending) == 1 and pending[0].message.content[0] == TextContent("older")

        result = await session.run(UserMessage("newer"))
        assert result.status is RunStatus.COMPLETED
        user_text = [
            message.content[0].text
            for message in session.messages
            if isinstance(message, UserMessage)
        ]
        assert user_text == ["first", "older", "newer"]
        assert await session.pending_inputs() == ()

    asyncio.run(check())


def test_commit_and_after_tool_failures_retain_uncommitted_effects() -> None:
    async def check() -> None:
        call = ToolCall("call", "act", FrozenJsonObject())

        async def handler(arguments, context):
            return ToolTextContent("applied")

        registry = ToolRegistry(
            (
                Tool(
                    ToolDefinition("act", "Act.", FrozenJsonObject({"type": "object"})),
                    handler,
                    effect_kind=ToolEffectKind.SIDE_EFFECTING,
                ),
            )
        )

        class CommitFails(Session):
            async def commit_exchange(self, run_id, assistant, results):
                raise RuntimeError("injected commit failure")

        commit_session = CommitFails(Agent(Replies((AssistantMessage(tool_calls=(call,)),)), tool_registry=registry))
        commit_run = commit_session.start(UserMessage("go"))
        commit_result = await commit_run.result()
        commit_events = [event async for event in commit_run.subscribe()]
        assert commit_result.status is RunStatus.FAILED
        assert commit_result.error is not None and commit_result.error.code == "transcript_commit_error"
        assert len(commit_result.effects) == 1
        assert not commit_result.effects[0].transcript_committed and not commit_result.retry_safe
        assert [message.role for message in commit_session.messages] == ["user"]
        assert all(event.type != "tool_batch.committed" for event in commit_events)

        class Hook:
            async def after_tool(self, context, result):
                raise RuntimeError("injected hook failure")

        async def acknowledgement_lost(arguments, context):
            raise ConnectionError("acknowledgement lost")

        uncertain_registry = ToolRegistry(
            (
                Tool(
                    ToolDefinition("act", "Act.", FrozenJsonObject({"type": "object"})),
                    acknowledgement_lost,
                    effect_kind=ToolEffectKind.SIDE_EFFECTING,
                ),
            )
        )
        hook_session = Agent(
            Replies((AssistantMessage(tool_calls=(call,)),)),
            tool_registry=uncertain_registry,
            hooks=(Hook(),),
        ).new_session()
        hook_result = await hook_session.run(UserMessage("go"))
        assert hook_result.status is RunStatus.FAILED
        assert hook_result.error is not None and hook_result.error.code == "hook_error"
        assert len(hook_result.effects) == 1
        assert hook_result.effects[0].status is ToolEffectStatus.UNKNOWN
        assert not hook_result.effects[0].transcript_committed and not hook_result.retry_safe
        assert [message.role for message in hook_session.messages] == ["user"]

        invalid_registry = ToolRegistry(
            (
                Tool(
                    ToolDefinition("act", "Act.", FrozenJsonObject({"type": "object"})),
                    lambda arguments, context: {"legacy": True},
                    effect_kind=ToolEffectKind.SIDE_EFFECTING,
                ),
            )
        )
        invalid_session = Agent(
            Replies((AssistantMessage(tool_calls=(call,)),)), tool_registry=invalid_registry
        ).new_session()
        invalid_result = await invalid_session.run(UserMessage("go"))
        assert invalid_result.status is RunStatus.FAILED
        assert invalid_result.error is not None and invalid_result.error.code == "tool_contract_error"
        assert len(invalid_result.effects) == 1
        assert invalid_result.effects[0].status is ToolEffectStatus.UNKNOWN
        assert not invalid_result.effects[0].transcript_committed and not invalid_result.retry_safe
        assert [message.role for message in invalid_session.messages] == ["user"]

    asyncio.run(check())


def test_pending_inputs_wait_for_model_boundary_and_keep_sequence() -> None:
    async def check() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingFirst(Replies):
            def __init__(self) -> None:
                super().__init__((AssistantMessage("first"), AssistantMessage("second")))
                self.contexts: list[ModelContext] = []

            async def stream(self, context, settings=None):
                self.contexts.append(context)
                message = next(self.replies)
                yield ResponseStarted("response", 0)
                if len(self.contexts) == 1:
                    started.set()
                    await release.wait()
                yield TextDelta(1, message.content[0].text)
                yield ResponseCompleted(2, ModelResponse(message, FinishReason.STOP))

        model = BlockingFirst()
        session = Agent(model).new_session()
        run = session.start(UserMessage("initial"))
        await started.wait()
        first = await session.steer(UserMessage("steer"))
        second = await session.follow_up(UserMessage("follow"))
        assert (first.sequence, second.sequence) == (1, 2)
        assert [message.role for message in session.messages] == ["user"]
        release.set()
        assert (await run.result()).status is RunStatus.COMPLETED
        assert [message.role for message in session.messages] == ["user", "assistant", "user", "user", "assistant"]
        tail = model.contexts[1].segments[-2:]
        assert all(isinstance(segment, MessageSegment) for segment in tail)
        assert [segment.message.content[0].text for segment in tail if isinstance(segment, MessageSegment)] == ["steer", "follow"]

    asyncio.run(check())


def test_pending_input_during_tool_waits_for_atomic_exchange() -> None:
    async def check() -> None:
        call = ToolCall("call", "lookup", FrozenJsonObject())
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(arguments, context):
            started.set()
            await release.wait()
            return ToolTextContent("done")

        registry = ToolRegistry(
            (Tool(ToolDefinition("lookup", "Lookup.", FrozenJsonObject({"type": "object"})), handler),)
        )
        session = Agent(
            Replies((AssistantMessage(tool_calls=(call,)), AssistantMessage("finished"))),
            tool_registry=registry,
        ).new_session()
        run = session.start(UserMessage("initial"))
        await started.wait()
        await session.steer(UserMessage("during tool"))
        assert [message.role for message in session.messages] == ["user"]
        release.set()
        assert (await run.result()).status is RunStatus.COMPLETED
        assert [message.role for message in session.messages] == ["user", "assistant", "tool", "user", "assistant"]

    asyncio.run(check())


def test_concurrent_completion_does_not_reorder_transcript_results() -> None:
    async def check() -> None:
        calls = tuple(ToolCall(name, "lookup", FrozenJsonObject({"delay": delay})) for name, delay in (("a", 0.03), ("b", 0.01), ("c", 0.02)))

        async def handler(arguments, context):
            await asyncio.sleep(arguments["delay"])
            return ToolTextContent(str(arguments["delay"]))

        definition = ToolDefinition(
            "lookup",
            "Lookup.",
            FrozenJsonObject(
                {
                    "type": "object",
                    "properties": {"delay": {"type": "number"}},
                    "required": ["delay"],
                    "additionalProperties": False,
                }
            ),
        )
        registry = ToolRegistry((Tool(definition, handler, ToolExecutionMode.CONCURRENT),))
        session = Agent(
            Replies((AssistantMessage(tool_calls=calls), AssistantMessage("done"))),
            tool_registry=registry,
        ).new_session()
        result = await session.run(UserMessage("go"))
        assert result.status is RunStatus.COMPLETED
        committed = [message for message in session.messages if isinstance(message, ToolResultMessage)]
        assert [message.tool_call_id for message in committed] == ["a", "b", "c"]
        assert [effect.call_id for effect in result.effects] == ["a", "b", "c"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "content",
    (
        ImageContent(BytesSource(b"image"), "image/png"),
        AudioContent(BytesSource(b"audio"), "audio/wav"),
        FileContent(BytesSource(b"file"), "application/pdf", "file.pdf"),
    ),
)
def test_unsupported_modalities_fail_without_model_output(content: object) -> None:
    async def check() -> None:
        class TextOnly(Replies):
            capabilities = ModelCapabilities(
                frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False
            )

            def __init__(self) -> None:
                super().__init__(())
                self.called = False

            async def stream(self, context, settings=None):
                self.called = True
                yield  # pragma: no cover

        model = TextOnly()
        session = Agent(model).new_session()
        run = session.start(UserMessage((content,)))
        result = await run.result()
        events = [event async for event in run.subscribe()]
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "unsupported_input_modality"
        assert not model.called
        assert [message.role for message in session.messages] == ["user"]
        assert events[-1].type == "run.failed" and run.state.error == result.error

    asyncio.run(check())


def test_failed_termination_reason_matches_result_state_and_event() -> None:
    async def assert_failed(run, code: str) -> None:
        result = await run.result()
        events = [event async for event in run.subscribe()]
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == code
        assert run.state.status is RunStatus.FAILED and run.state.error == result.error
        assert events[-1].type == "run.failed"
        assert events[-1].payload["status"] == "failed"
        assert events[-1].payload["error_code"] == code

    async def check() -> None:
        call = ToolCall("call", "lookup", FrozenJsonObject())

        async def handler(arguments, context):
            return ToolTextContent("done")

        registry = ToolRegistry(
            (Tool(ToolDefinition("lookup", "Lookup.", FrozenJsonObject({"type": "object"})), handler),)
        )
        max_turns = Agent(
            Replies((AssistantMessage(tool_calls=(call,)),)), tool_registry=registry
        ).new_session().start(UserMessage("go"), config=RunConfig(max_turns=1))
        await assert_failed(max_turns, "max_turns")

        class BrokenContext:
            async def prepare(self, request, cancellation):
                raise RuntimeError("context broke")

        context_error = Agent(
            Replies(()), context_manager=BrokenContext()
        ).new_session().start(UserMessage("go"))
        await assert_failed(context_error, "context_error")

        class BrokenModel:
            capabilities = Replies.capabilities

            async def stream(self, context, settings=None):
                raise RuntimeError("model broke")
                yield  # pragma: no cover

        model_error = Agent(BrokenModel()).new_session().start(UserMessage("go"))
        await assert_failed(model_error, "model_error")

    asyncio.run(check())
