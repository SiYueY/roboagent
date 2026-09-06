from __future__ import annotations

import asyncio

from examples.coding.client import WorkerClient
from examples.coding.harness import CodingConfig
from roboagent.runtime import RuntimeCancellation


class FakeExecution:
    deadline = None
    cancellation = RuntimeCancellation()

    def add_retry_blocker(self, code, message):
        pass


def test_worker_handshake_persistent_state_and_final() -> None:
    async def check() -> None:
        worker = WorkerClient(CodingConfig())
        try:
            first = await worker.execute("x = 7\nprint(x)", (), FakeExecution())
            second = await worker.execute("final_answer(x)", (), FakeExecution())
            assert first["stdout"] == "7\n"
            assert second["final"] == {"kind": "json", "value": 7}
            assert second["is_final"] is True
        finally:
            await worker.close()

    asyncio.run(check())


def test_worker_timeout_resets_generation() -> None:
    async def check() -> None:
        worker = WorkerClient(CodingConfig(execution_timeout=0.05))
        try:
            try:
                await worker.execute("while True:\n    pass", (), FakeExecution())
            except Exception as exc:
                assert getattr(exc, "code", None) == "execution_timeout"
            assert worker.generation == 2
            assert worker.pending_reset_notice
            assert not worker.alive
        finally:
            await worker.close()

    asyncio.run(check())
