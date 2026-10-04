"""Session-level recovery helpers: clear_pending, delete, and open-by-id."""

from __future__ import annotations

import asyncio

import pytest

from roboagent.agent import (
    Agent,
    LocalSessionRepository,
    Session,
    SessionBusyError,
    SessionNotFoundError,
)
from roboagent.context import PromptInput
from roboagent.message import AssistantMessage, UserMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
)
from roboagent.runtime import Modality


class EchoModel:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False
    )

    async def stream(self, context, settings=None):
        yield ResponseStarted("response", 0)
        yield TextDelta(1, "ok")
        yield ResponseCompleted(
            2, ModelResponse(AssistantMessage("ok"), FinishReason.STOP)
        )


def _agent() -> Agent:
    return Agent(EchoModel(), prompt=PromptInput("Help."))


def test_clear_pending_discards_and_persists(tmp_path) -> None:
    async def check() -> None:
        repository = LocalSessionRepository(tmp_path / "sessions")
        session = _agent().new_session(repository=repository)
        await session.steer(UserMessage("steer me"))
        await session.follow_up(UserMessage("and me"))
        assert len(await session.pending_inputs()) == 2

        receipts = await session.clear_pending()
        assert [item.sequence for item in receipts] == [1, 2]
        assert await session.pending_inputs() == ()

        # The cleared queue must be durable, not only in memory.
        reloaded = await repository.load(session.session_id)
        assert reloaded is not None
        assert reloaded.pending == ()

        # Clearing an already empty queue is a no-op.
        assert await session.clear_pending() == ()

    asyncio.run(check())


def test_clear_pending_rejects_active_run(tmp_path) -> None:
    async def check() -> None:
        release = asyncio.Event()

        class Blocking(EchoModel):
            async def stream(self, context, settings=None):
                yield ResponseStarted("response", 0)
                await release.wait()
                yield TextDelta(1, "ok")
                yield ResponseCompleted(
                    2, ModelResponse(AssistantMessage("ok"), FinishReason.STOP)
                )

        session = Agent(Blocking(), prompt=PromptInput("Help.")).new_session(
            repository=LocalSessionRepository(tmp_path / "sessions")
        )
        run = session.start(UserMessage("go"))
        await asyncio.sleep(0)
        with pytest.raises(SessionBusyError):
            await session.clear_pending()
        release.set()
        await run.result()

    asyncio.run(check())


def test_delete_removes_snapshot_and_is_idempotent(tmp_path) -> None:
    async def check() -> None:
        repository = LocalSessionRepository(tmp_path / "sessions")
        session = _agent().new_session(repository=repository)
        run = session.start(UserMessage("go"))
        await run.result()

        session_id = session.session_id
        assert await repository.load(session_id) is not None

        await session.delete()
        assert await repository.load(session_id) is None

        # A second delete of an absent record stays a no-op.
        await session.delete()

    asyncio.run(check())


def test_open_by_id_restores_messages_and_raises_when_absent(tmp_path) -> None:
    async def check() -> None:
        repository = LocalSessionRepository(tmp_path / "sessions")
        agent = _agent()
        session = agent.new_session(repository=repository)
        run = session.start(UserMessage("go"))
        await run.result()
        expected = [message.message_id for message in session.messages]

        reopened = await Session.open(
            agent=agent, session_id=session.session_id, repository=repository
        )
        assert reopened.session_id == session.session_id
        assert [message.message_id for message in reopened.messages] == expected

        with pytest.raises(SessionNotFoundError):
            await Session.open(
                agent=agent, session_id="missing", repository=repository
            )

    asyncio.run(check())
