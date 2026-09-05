from __future__ import annotations

import asyncio

import pytest

from roboagent.message import FrozenJsonObject, ToolCall, canonical_json_digest
from roboagent.runtime import RuntimeCancellation
from roboagent.runtime.event import RunEventEmitter
from roboagent.tool import (
    ApprovalDecision,
    ApprovalResponse,
    ApprovalSettings,
    Tool,
    ToolBatchAborted,
    ToolBatchCancelled,
    ToolContext,
    ToolDecision,
    ToolDefinition,
    ToolExecutionMode,
    ToolExecutor,
    ToolExecutorConfig,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
)


def _definition() -> ToolDefinition:
    return ToolDefinition("work", "Do work.", FrozenJsonObject({"type": "object"}))


def _context(cancellation=None) -> ToolContext:
    return ToolContext("run", "session", cancellation or RuntimeCancellation())


class _RequireApproval:
    async def evaluate(self, call, tool, context):
        return ToolPolicyDecision(ToolDecision.REQUIRE_APPROVAL, "robot motion")


class _Approver:
    def __init__(self, decision=ApprovalDecision.APPROVE) -> None:
        self.decision = decision
        self.requests = []

    async def request(self, request, cancellation):
        self.requests.append(request)
        return ApprovalResponse(request.approval_id, request.arguments_digest, self.decision)


def test_approval_request_is_immutable_bound_and_approved_tool_executes() -> None:
    async def check() -> None:
        calls = 0

        async def handler(arguments, context):
            nonlocal calls
            calls += 1
            return ToolTextContent("done")

        approver = _Approver()
        call = ToolCall("call", "work", FrozenJsonObject({"z": 1, "a": 2}))
        batch = await ToolExecutor(
            registry=ToolRegistry((Tool(_definition(), handler),)),
            policy=_RequireApproval(),
            approval_provider=approver,
        ).execute((call,), _context())
        assert calls == 1 and len(batch.effects) == 1
        request = approver.requests[0]
        assert request.run_id == "run" and request.session_id == "session"
        assert request.arguments is call.arguments
        assert request.arguments_digest == canonical_json_digest({"a": 2, "z": 1})
        with pytest.raises(TypeError):
            request.arguments["a"] = 3  # type: ignore[index]

    asyncio.run(check())


def test_approval_reject_and_timeout_never_start_tool_or_create_effect() -> None:
    async def check() -> None:
        starts = 0

        async def handler(arguments, context):
            nonlocal starts
            starts += 1
            return ToolTextContent("done")

        tool = Tool(_definition(), handler)
        rejected = await ToolExecutor(
            registry=ToolRegistry((tool,)), policy=_RequireApproval(), approval_provider=_Approver(ApprovalDecision.REJECT)
        ).execute((ToolCall("reject", "work"),), _context())
        assert rejected.results[0].error.code == "approval_rejected"
        assert rejected.effects == () and starts == 0

        class Waiting:
            async def request(self, request, cancellation):
                await asyncio.Event().wait()

        timed = await ToolExecutor(
            registry=ToolRegistry((tool,)),
            policy=_RequireApproval(),
            approval_provider=Waiting(),
            approval_settings=ApprovalSettings(0.001),
        ).execute((ToolCall("timeout", "work"),), _context())
        assert timed.results[0].error.code == "approval_timeout"
        assert timed.effects == () and starts == 0

    asyncio.run(check())


@pytest.mark.parametrize("field", ["approval_id", "arguments_digest"])
def test_approval_identity_mismatch_aborts_without_execution(field: str) -> None:
    async def check() -> None:
        starts = 0

        async def handler(arguments, context):
            nonlocal starts
            starts += 1
            return ToolTextContent("done")

        class Mismatch:
            async def request(self, request, cancellation):
                values = {"approval_id": request.approval_id, "arguments_digest": request.arguments_digest}
                values[field] = "mismatch"
                return ApprovalResponse(values["approval_id"], values["arguments_digest"], ApprovalDecision.APPROVE)

        executor = ToolExecutor(
            registry=ToolRegistry((Tool(_definition(), handler),)),
            policy=_RequireApproval(),
            approval_provider=Mismatch(),
        )
        with pytest.raises(ToolBatchAborted) as caught:
            await executor.execute((ToolCall("call", "work"),), _context())
        assert caught.value.reason.code == "approval_mismatch"
        assert caught.value.effects == () and starts == 0

    asyncio.run(check())


def test_approval_provider_failure_aborts_without_execution_or_effect() -> None:
    async def check() -> None:
        starts = 0

        async def handler(arguments, context):
            nonlocal starts
            starts += 1
            return ToolTextContent("done")

        class Broken:
            async def request(self, request, cancellation):
                raise ConnectionError("approval backend unavailable")

        executor = ToolExecutor(
            registry=ToolRegistry((Tool(_definition(), handler),)),
            policy=_RequireApproval(),
            approval_provider=Broken(),
        )
        with pytest.raises(ToolBatchAborted) as caught:
            await executor.execute((ToolCall("call", "work"),), _context())
        assert caught.value.reason.code == "approval_error"
        assert caught.value.effects == () and starts == 0

    asyncio.run(check())


def test_approval_invalid_response_aborts_without_execution_or_effect() -> None:
    async def check() -> None:
        starts = 0

        async def handler(arguments, context):
            nonlocal starts
            starts += 1
            return ToolTextContent("done")

        class Invalid:
            async def request(self, request, cancellation):
                return {"decision": "approve"}

        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(
                registry=ToolRegistry((Tool(_definition(), handler),)),
                policy=_RequireApproval(),
                approval_provider=Invalid(),
            ).execute((ToolCall("call", "work"),), _context())
        assert caught.value.reason.code == "approval_error"
        assert caught.value.effects == () and starts == 0

    asyncio.run(check())


def test_approval_events_are_ordered_json_safe_and_redacted() -> None:
    async def check() -> None:
        events = RunEventEmitter("run")
        arguments = FrozenJsonObject({"password": "do-not-publish"})
        await ToolExecutor(
            registry=ToolRegistry((Tool(_definition(), lambda arguments, context: ToolTextContent("done")),)),
            policy=_RequireApproval(),
            approval_provider=_Approver(),
            events=events,
        ).execute((ToolCall("call", "work", arguments),), _context())
        approval_events = [event for event in events.history if event.type.startswith("approval.")]
        assert [event.type for event in approval_events] == ["approval.requested", "approval.resolved"]
        assert approval_events[1].payload["outcome"] == "approved"
        assert "do-not-publish" not in repr(tuple(event.payload for event in approval_events))
        assert all("arguments" not in event.payload and "reason" not in event.payload for event in approval_events)

    asyncio.run(check())


def test_cancel_during_approval_has_no_effect() -> None:
    async def check() -> None:
        cancellation = RuntimeCancellation()
        waiting = asyncio.Event()

        class Provider:
            async def request(self, request, token):
                waiting.set()
                await asyncio.Event().wait()

        executor = ToolExecutor(
            registry=ToolRegistry((Tool(_definition(), lambda arguments, context: ToolTextContent("never")),)),
            policy=_RequireApproval(),
            approval_provider=Provider(),
        )
        task = asyncio.create_task(executor.execute((ToolCall("call", "work"),), _context(cancellation)))
        await waiting.wait()
        cancellation.cancel()
        with pytest.raises(ToolBatchCancelled) as caught:
            await task
        assert caught.value.effects == ()

    asyncio.run(check())


def test_serial_policy_observes_previous_execution_and_later_fail_does_not_rollback() -> None:
    async def check() -> None:
        executed = []

        async def handler(arguments, context):
            executed.append("first")
            return ToolTextContent("done")

        class Policy:
            async def evaluate(self, call, tool, context):
                if call.id == "second":
                    assert executed == ["first"]
                    return ToolPolicyDecision(ToolDecision.FAIL_RUN)
                return ToolDecision.ALLOW

        executor = ToolExecutor(registry=ToolRegistry((Tool(_definition(), handler),)), policy=Policy())
        with pytest.raises(ToolBatchAborted) as caught:
            await executor.execute((ToolCall("first", "work"), ToolCall("second", "work")), _context())
        assert executed == ["first"]
        assert caught.value.reason.code == "policy_fail_run"
        assert [effect.call_id for effect in caught.value.effects] == ["first"]

    asyncio.run(check())


def test_all_concurrent_prepares_in_order_and_fail_run_prevents_batch_start() -> None:
    async def check() -> None:
        prepared = []
        started = []

        async def handler(arguments, context):
            started.append(arguments["index"])
            return ToolTextContent("done")

        class Policy:
            async def evaluate(self, call, tool, context):
                prepared.append(call.id)
                if call.id == "two":
                    return ToolPolicyDecision(ToolDecision.FAIL_RUN)
                return ToolPolicyDecision(ToolDecision.ALLOW)

        tool = Tool(_definition(), handler, ToolExecutionMode.CONCURRENT)
        calls = (
            ToolCall("one", "work", FrozenJsonObject({"index": 1})),
            ToolCall("two", "work", FrozenJsonObject({"index": 2})),
        )
        with pytest.raises(ToolBatchAborted):
            await ToolExecutor(registry=ToolRegistry((tool,)), policy=Policy()).execute(calls, _context())
        assert prepared == ["one", "two"]
        assert started == []

    asyncio.run(check())


def test_all_concurrent_approvals_are_ordered_then_execution_is_bounded() -> None:
    async def check() -> None:
        active = 0
        peak = 0

        async def handler(arguments, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return ToolTextContent(str(arguments["index"]))

        approver = _Approver()
        tool = Tool(_definition(), handler, ToolExecutionMode.CONCURRENT)
        calls = tuple(ToolCall(str(index), "work", FrozenJsonObject({"index": index})) for index in range(4))
        batch = await ToolExecutor(
            registry=ToolRegistry((tool,)),
            policy=_RequireApproval(),
            approval_provider=approver,
            config=ToolExecutorConfig(max_concurrency=2),
        ).execute(calls, _context())
        assert [request.tool_call_id for request in approver.requests] == ["0", "1", "2", "3"]
        assert peak == 2
        assert [result.call_id for result in batch.results] == ["0", "1", "2", "3"]

    asyncio.run(check())
