from __future__ import annotations

import asyncio

from roboagent import Agent
from roboagent.agent import InMemorySessionRepository, RunConfig, SessionPersistenceError
from roboagent.context import (
    CompactionUpdate,
    ContextBudgetError,
    ContextSummary,
    MessageSegment,
    ModelContext,
    PreparedContext,
    SummarySegment,
)
from roboagent.message import AssistantMessage, FrozenJsonObject, ToolCall, UserMessage, canonical_message_digest
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
)
from roboagent.runtime import RunStatus
from roboagent.tool import (
    ApprovalProvider,
    Tool,
    ToolDecision,
    ToolDefinition,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
)


class _CompletingModel:
    capabilities = ModelCapabilities(tool_calling=True)

    async def stream(self, context, settings=None):
        yield ResponseStarted("response", 0)
        yield ResponseCompleted(1, ModelResponse(AssistantMessage("done"), FinishReason.STOP))


def test_context_compaction_events_are_emitted_only_from_observed_outcomes() -> None:
    async def check() -> None:
        message = UserMessage("history")
        summary = ContextSummary(0, 1, canonical_message_digest((message,)), "summary", 1)

        class Manager:
            async def prepare(self, request, cancellation):
                return PreparedContext(
                    ModelContext(None, (SummarySegment(summary.text),), ()),
                    Usage(0, 0, 0),
                    compaction_update=CompactionUpdate(summary, None),
                )

        session = Agent(_CompletingModel(), context_manager=Manager()).new_session(messages=(message,))
        run = session.start()
        assert (await run.result()).status is RunStatus.COMPLETED
        history = [event async for event in run.subscribe()]
        events = [event for event in history if event.type.startswith("context.")]
        assert [event.type for event in events] == ["context.compaction_completed"]
        assert events[0].payload["source_digest"] == summary.source_digest
        assert "summary" not in events[0].payload

        class ClearingManager:
            async def prepare(self, request, cancellation):
                return PreparedContext(
                    ModelContext(None, tuple(MessageSegment(item) for item in request.snapshot.transcript), ()),
                    Usage(0, 0, 0),
                    compaction_update=CompactionUpdate(None, summary.source_digest),
                )

        clearing = Agent(_CompletingModel(), context_manager=ClearingManager()).new_session(
            messages=(message,)
        )
        await clearing.acquire_run("seed")
        assert await clearing.commit_compaction("seed", CompactionUpdate(summary, None))
        await clearing.release_run("seed")
        clearing_run = clearing.start()
        assert (await clearing_run.result()).status is RunStatus.COMPLETED
        clearing_history = [event async for event in clearing_run.subscribe()]
        cleared = next(
            event for event in clearing_history if event.type == "context.compaction_completed"
        )
        assert cleared.payload == FrozenJsonObject({"outcome": "cleared"})

        class FailingManager:
            async def prepare(self, request, cancellation):
                raise ContextBudgetError("context_compaction_error", "summarizer_failure")

        failed_session = Agent(_CompletingModel(), context_manager=FailingManager()).new_session()
        failed_run = failed_session.start(UserMessage("go"))
        assert (await failed_run.result()).status is RunStatus.FAILED
        failed_history = [event async for event in failed_run.subscribe()]
        assert [
            event.type for event in failed_history if event.type.startswith("context.")
        ] == ["context.compaction_failed"]

    asyncio.run(check())


def test_persistence_events_report_revision_without_session_content() -> None:
    async def check() -> None:
        repository = InMemorySessionRepository()
        session = Agent(_CompletingModel()).new_session(session_id="session", repository=repository)
        run = session.start(UserMessage("secret transcript"))
        assert (await run.result()).status is RunStatus.COMPLETED
        history = [event async for event in run.subscribe()]
        events = [event for event in history if event.type.startswith("session.")]
        assert len(events) == 2
        assert all(event.type == "session.persisted" for event in events)
        assert [event.payload["revision"] for event in events] == [1, 2]
        assert "secret transcript" not in repr(tuple(event.payload for event in events))

        class BrokenRepository(InMemorySessionRepository):
            async def save(self, snapshot, *, expected_revision):
                raise OSError("disk secret")

        failed = Agent(_CompletingModel()).new_session(
            session_id="failed", repository=BrokenRepository()
        )
        failed_run = failed.start(UserMessage("private"))
        result = await failed_run.result()
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == SessionPersistenceError.code
        failed_history = [event async for event in failed_run.subscribe()]
        failure = next(
            event for event in failed_history if event.type == "session.persistence_failed"
        )
        assert failure.payload == FrozenJsonObject(
            {"required_revision": 1, "error_code": "session_persistence_error"}
        )
        assert "disk secret" not in repr(failure.payload)

    asyncio.run(check())


def test_run_timeout_during_approval_never_starts_tool_or_creates_effect() -> None:
    async def check() -> None:
        call = ToolCall("call", "work")
        started = 0

        class CallingModel:
            capabilities = ModelCapabilities(tool_calling=True)

            async def stream(self, context, settings=None):
                yield ResponseStarted("response", 0)
                yield ToolCallStarted(1, 0, call.id, call.name)
                yield ToolCallCompleted(2, 0, call)
                yield ResponseCompleted(
                    3, ModelResponse(AssistantMessage(tool_calls=(call,)), FinishReason.TOOL_CALL)
                )

        async def handler(arguments, context):
            nonlocal started
            started += 1
            return ToolTextContent("done")

        class Policy:
            async def evaluate(self, selected_call, tool, context):
                return ToolPolicyDecision(ToolDecision.REQUIRE_APPROVAL, "sensitive reason")

        class WaitingApproval:
            async def request(self, request, cancellation):
                await asyncio.Event().wait()

        provider: ApprovalProvider = WaitingApproval()
        tool = Tool(
            ToolDefinition("work", "Work.", FrozenJsonObject({"type": "object"})), handler
        )
        session = Agent(
            CallingModel(),
            tool_registry=ToolRegistry((tool,)),
            tool_policy=Policy(),
            approval_provider=provider,
        ).new_session()
        run = session.start(UserMessage("go"), config=RunConfig(timeout=0.01))
        result = await run.result()
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "timeout"
        assert result.effects == () and started == 0
        event_types = [event.type async for event in run.subscribe()]
        assert event_types.count("approval.requested") == 1
        assert event_types.count("approval.resolved") == 1
        assert "tool.started" not in event_types

    asyncio.run(check())
