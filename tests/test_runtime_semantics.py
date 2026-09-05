from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent, Session
from roboagent.agent import HookDecision, RunConfig, SessionBusyError
from roboagent.context import PromptInput
from roboagent.message import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
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
        assert result.retry_safe is False
        assert [message.role for message in session.messages] == ["user"]

    asyncio.run(check())


def test_input_enqueued_during_cleanup_remains_for_next_run() -> None:
    async def check() -> None:
        session = None

        class Hook:
            async def on_run_end(self, context):
                assert session is not None
                await session.follow_up(UserMessage("next"))

        session = Agent(Replies((AssistantMessage("done"),)), hooks=(Hook(),)).new_session()
        result = await session.run(UserMessage("first"))
        assert result.status is RunStatus.COMPLETED
        pending = await session.pending_inputs()
        assert len(pending) == 1 and pending[0].message.content[0] == TextContent("next")

    asyncio.run(check())
