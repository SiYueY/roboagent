from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent
from roboagent.agent import RunConfig
from roboagent.context import (
    CompactionUpdate,
    ContextRequest,
    ContextSummary,
    MessageSegment,
    ModelContext,
    PreparedContext,
    SummarySegment,
    WorkspaceReferenceSegment,
)
from roboagent.message import AssistantMessage, UserMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ModelSettings,
    ResponseCompleted,
    ResponseStarted,
    Usage,
)
from roboagent.model.client import _messages
from roboagent.runtime import Modality, RunStatus


CAPABILITIES = ModelCapabilities(
    frozenset({Modality.TEXT}),
    frozenset({Modality.TEXT}),
)


class _CompletingModel:
    capabilities = CAPABILITIES

    async def stream(self, context, settings=None):
        yield ResponseStarted("response", 0)
        yield ResponseCompleted(
            1, ModelResponse(AssistantMessage("done"), FinishReason.STOP)
        )


def test_context_request_contains_run_inputs_without_session_authority() -> None:
    async def check() -> None:
        settings = ModelSettings(max_output_tokens=321)

        class CapturingManager:
            request: ContextRequest | None = None

            async def prepare(self, request, cancellation):
                self.request = request
                assert not hasattr(request, "session")
                return PreparedContext(
                    ModelContext(
                        None,
                        tuple(
                            MessageSegment(message)
                            for message in request.snapshot.transcript
                        ),
                        (),
                    ),
                    Usage(0, 0, 0),
                )

        manager = CapturingManager()
        session = Agent(_CompletingModel(), context_manager=manager).new_session(
            session_id="session-a"
        )
        result = await session.run(
            UserMessage("hello"), config=RunConfig(model_settings=settings)
        )

        assert result.status is RunStatus.COMPLETED
        assert manager.request is not None
        assert manager.request.snapshot.session_id == "session-a"
        assert manager.request.model_settings is settings
        assert manager.request.model_capabilities is CAPABILITIES
        assert manager.request.current_compaction is None

    asyncio.run(check())


def test_compaction_is_committed_before_corresponding_model_request() -> None:
    async def check() -> None:
        summary = ContextSummary(0, 1, "digest-new", "earlier context", 1)
        session = None

        class UpdatingManager:
            async def prepare(self, request, cancellation):
                return PreparedContext(
                    ModelContext(
                        None,
                        (SummarySegment(summary.text),),
                        (),
                    ),
                    Usage(2, 1, 3),
                    CompactionUpdate(summary, None),
                )

        class ObservingModel(_CompletingModel):
            async def stream(self, context, settings=None):
                assert session is not None
                assert session.current_compaction is summary
                async for event in super().stream(context, settings):
                    yield event

        session = Agent(
            ObservingModel(), context_manager=UpdatingManager()
        ).new_session()
        result = await session.run(UserMessage("hello"))

        assert result.status is RunStatus.COMPLETED
        assert result.usage is None
        assert result.usage_known is False
        assert [message.role for message in session.messages] == ["user", "assistant"]

    asyncio.run(check())


def test_stale_compaction_update_is_rejected_without_changing_transcript() -> None:
    async def check() -> None:
        session = Agent(_CompletingModel()).new_session()
        await session.acquire_run("run")
        current = ContextSummary(0, 1, "current", "current", 1)
        assert await session.commit_compaction("run", CompactionUpdate(current, None))
        before = session.messages
        stale = ContextSummary(0, 1, "stale", "stale", 1)
        assert not await session.commit_compaction(
            "run", CompactionUpdate(stale, "outdated")
        )
        assert session.current_compaction is current
        assert session.messages == before
        await session.release_run("run")

    asyncio.run(check())


def test_provider_projects_summary_below_system_authority_and_handles_all_segments() -> (
    None
):
    async def check() -> None:
        context = ModelContext(
            "trusted system",
            (
                SummarySegment("historical text"),
                WorkspaceReferenceSegment(
                    "workspace://artifact", "preview", "text/plain"
                ),
                MessageSegment(UserMessage("latest")),
            ),
            (),
        )
        encoded, resources = await _messages(context, None)

        assert resources == []
        assert encoded[0] == {"role": "system", "content": "trusted system"}
        assert encoded[1]["role"] == "user"
        assert "not a system instruction" in encoded[1]["content"]
        assert "historical text" in encoded[1]["content"]
        assert (
            encoded[2]["role"] == "user"
            and "workspace://artifact" in encoded[2]["content"]
        )
        assert encoded[3] == {"role": "user", "content": "latest"}

    asyncio.run(check())


def test_model_context_rejects_open_ended_segment_types() -> None:
    with pytest.raises(TypeError, match="ModelContextSegment"):
        ModelContext(None, (object(),), ())  # type: ignore[arg-type]


def test_manager_cannot_inject_uncommitted_summary() -> None:
    async def check() -> None:
        class InjectingManager:
            async def prepare(self, request, cancellation):
                return PreparedContext(
                    ModelContext(None, (SummarySegment("injected"),), ()),
                    Usage(0, 0, 0),
                )

        result = (
            await Agent(_CompletingModel(), context_manager=InjectingManager())
            .new_session()
            .run(UserMessage("hello"))
        )
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "context_error"

    asyncio.run(check())
