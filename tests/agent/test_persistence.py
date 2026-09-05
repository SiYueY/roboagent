from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent
from roboagent.agent import (
    InMemorySessionRepository,
    InputReceipt,
    JsonSessionSnapshotCodec,
    LocalSessionRepository,
    PendingInput,
    SessionConflictError,
    SessionCorruptedError,
    SessionPersistenceError,
    SessionSnapshot,
)
from roboagent.context import ContextSummary
from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    BytesSource,
    FrozenJsonObject,
    ImageContent,
    JsonContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolResultStatus,
    UserMessage,
    canonical_message_digest,
)
from roboagent.model import FinishReason, ModelCapabilities, ModelResponse, ResponseCompleted, ResponseStarted, ToolCallCompleted, ToolCallStarted
from roboagent.runtime import RunStatus
from roboagent.tool import Tool, ToolDefinition, ToolEffectKind, ToolRegistry, ToolTextContent


class _UnusedModel:
    capabilities = ModelCapabilities()

    async def stream(self, context, settings=None):
        raise AssertionError("not used")
        yield


def _snapshot(revision: int, *, session_id: str = "session") -> SessionSnapshot:
    return SessionSnapshot(1, session_id, revision, 0, (), ())


def test_snapshot_codec_round_trips_all_current_content_and_ordered_json() -> None:
    call = ToolCall("call", "work", FrozenJsonObject((("z", 1), ("a", [2]))))
    artifact = ArtifactReferenceContent(
        "workspace://blobs/sha256/" + "a" * 64, "text/plain", 4, "sha256:" + "a" * 64, "data"
    )
    messages = (
        UserMessage((TextContent("hello"), JsonContent(FrozenJsonObject((("z", 1), ("a", 2)))), ImageContent(BytesSource(b"i"), "image/png"))),
        AssistantMessage(tool_calls=(call,)),
        ToolResultMessage("call", "work", ToolResultStatus.SUCCESS, (artifact, AudioContent(BytesSource(b"a"), "audio/wav"))),
    )
    pending = (PendingInput(InputReceipt("input", 7, "session"), UserMessage("later"), "follow_up"),)
    summary = ContextSummary(0, 1, canonical_message_digest(messages[:1]), "summary", 1)
    snapshot = SessionSnapshot(1, "session", 9, 7, messages, pending, summary, FrozenJsonObject((("z", 1), ("a", 2))))
    codec = JsonSessionSnapshotCodec()

    restored = codec.decode(codec.encode(snapshot))

    assert restored == snapshot
    assert list(restored.metadata) == ["z", "a"]
    assert canonical_message_digest(restored.messages[:1]) == summary.source_digest


def test_repository_cas_allows_revision_gap_and_rejects_stale_writer() -> None:
    async def check() -> None:
        repository = InMemorySessionRepository()
        assert await repository.save(_snapshot(10), expected_revision=None) == 10
        assert await repository.save(_snapshot(12), expected_revision=10) == 12
        with pytest.raises(SessionConflictError):
            await repository.save(_snapshot(13), expected_revision=10)
        with pytest.raises(SessionConflictError):
            await repository.save(_snapshot(12), expected_revision=12)

    asyncio.run(check())


def test_restore_preserves_pending_order_and_monotonic_sequence() -> None:
    async def check() -> None:
        pending = (
            PendingInput(InputReceipt("old", 5, "session"), UserMessage("old"), "steer"),
        )
        snapshot = SessionSnapshot(1, "session", 8, 5, (), pending)
        session = Agent(_UnusedModel()).new_session()
        restored = session.restore(agent=session.agent, snapshot=snapshot)
        assert restored.active_run_id is None
        receipt = await restored.follow_up(UserMessage("new"))
        assert receipt.sequence == 6
        assert [item.receipt.sequence for item in await restored.pending_inputs()] == [5, 6]
        assert restored.runtime_revision == 9 and restored.durable_revision == 8

    asyncio.run(check())


def test_restore_discards_only_invalid_compaction_and_rejects_bad_pending() -> None:
    messages = (UserMessage("truth"),)
    stale = ContextSummary(0, 1, "bad", "summary", 1)
    restored = Agent(_UnusedModel()).new_session().restore(
        agent=Agent(_UnusedModel()), snapshot=SessionSnapshot(1, "session", 1, 0, messages, (), stale)
    )
    assert restored.messages == messages and restored.current_compaction is None

    bad = SessionSnapshot(
        1,
        "session",
        1,
        2,
        (),
        (
            PendingInput(InputReceipt("two", 2, "session"), UserMessage("two"), "steer"),
            PendingInput(InputReceipt("one", 1, "session"), UserMessage("one"), "steer"),
        ),
    )
    with pytest.raises(SessionCorruptedError):
        Agent(_UnusedModel()).new_session().restore(agent=Agent(_UnusedModel()), snapshot=bad)


def test_persistence_failure_keeps_runtime_truth_and_next_save_uses_durable_revision() -> None:
    async def check() -> None:
        class FailsOnce(InMemorySessionRepository):
            failed = False
            expected = []

            async def save(self, snapshot, *, expected_revision):
                self.expected.append(expected_revision)
                if snapshot.revision == 1 and not self.failed:
                    self.failed = True
                    raise OSError("disk")
                return await super().save(snapshot, expected_revision=expected_revision)

        repository = FailsOnce()
        session = Agent(_UnusedModel()).new_session(session_id="session", repository=repository)
        with pytest.raises(SessionPersistenceError):
            await session.follow_up(UserMessage("accepted"))
        assert len(await session.pending_inputs()) == 1
        assert session.runtime_revision == 1 and session.durable_revision is None
        receipt = await session.steer(UserMessage("also accepted"))
        assert receipt.sequence == 2
        assert session.runtime_revision == 2 and session.durable_revision == 2
        assert repository.expected == [None, None]
        loaded = await repository.load("session")
        assert loaded is not None and loaded.revision == 2 and len(loaded.pending) == 2

    asyncio.run(check())


def test_local_repository_serializes_competing_cas_and_delete(tmp_path) -> None:
    async def check() -> None:
        first = LocalSessionRepository(tmp_path)
        second = LocalSessionRepository(tmp_path)
        await first.save(_snapshot(1), expected_revision=None)
        outcomes = await asyncio.gather(
            first.save(_snapshot(2), expected_revision=1),
            second.save(_snapshot(3), expected_revision=1),
            return_exceptions=True,
        )
        assert sum(isinstance(item, int) for item in outcomes) == 1
        assert sum(isinstance(item, SessionConflictError) for item in outcomes) == 1
        loaded = await first.load("session")
        assert loaded is not None
        with pytest.raises(SessionConflictError):
            await first.delete("session", expected_revision=1)
        await first.delete("session", expected_revision=loaded.revision)
        assert await first.load("session") is None
        assert not tuple(tmp_path.glob("*.tmp"))

    asyncio.run(check())


def test_tool_exchange_persistence_failure_keeps_committed_transcript_and_effect() -> None:
    async def check() -> None:
        call = ToolCall("call", "work", FrozenJsonObject())

        class Replies:
            capabilities = ModelCapabilities(tool_calling=True)

            async def stream(self, context, settings=None):
                    yield ResponseStarted("response", 0)
                    yield ToolCallStarted(1, 0, call.id, call.name)
                    yield ToolCallCompleted(2, 0, call)
                    yield ResponseCompleted(3, ModelResponse(AssistantMessage(tool_calls=(call,)), FinishReason.TOOL_CALL))

        class FailSecondSave(InMemorySessionRepository):
            async def save(self, snapshot, *, expected_revision):
                if snapshot.revision >= 2:
                    raise OSError("disk")
                return await super().save(snapshot, expected_revision=expected_revision)

        tool = Tool(
            ToolDefinition("work", "Do work.", FrozenJsonObject({"type": "object"})),
            lambda arguments, context: ToolTextContent("done"),
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
        )
        session = Agent(Replies(), tool_registry=ToolRegistry((tool,))).new_session(
            session_id="session", repository=FailSecondSave()
        )
        result = await session.run(UserMessage("go"))
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "session_persistence_error"
        assert [message.role for message in session.messages] == ["user", "assistant", "tool"]
        assert len(result.effects) == 1 and result.effects[0].transcript_committed

    asyncio.run(check())
