"""Physical dispatch regressions against the actual roserver operation contract."""

import asyncio
from dataclasses import replace

import pytest

from examples.embodied.mfr3duo_bridge import Mfr3duoBridge
from examples.embodied.contracts import decision_evidence
from roboagent.message import ToolCall, freeze_json_object
from roboagent.runtime import (
    ExecutionBudgetConfig,
    ExecutionScopeState,
    RuntimeCancellation,
)
from roboagent.runtime.execution import RuntimeRunExecutionContext
from roboagent.tool import ToolContext, ToolExecutionFailure, ToolExecutor, ToolRegistry
from roboagent.world import EntityRef, WorldEntryKey, WorldStatus
from tests.world.test_world import Clock, claim, observation, world


class Transport:
    def __init__(self):
        self.calls = []
        self.record = {
            "operation_id": "op_1",
            "robot_id": "robot",
            "status": "running",
            "finished_at": None,
            "error": None,
        }
        self.info_hook = None
        self.submit_hook = None

    async def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body))
        if path.endswith("/stop"):
            return {"stopped": True}
        if method == "POST":
            if self.submit_hook is not None:
                await self.submit_hook()
            return dict(self.record)
        if "/operations/" in path:
            return dict(self.record)
        if self.info_hook is not None:
            await self.info_hook()
        return {"connection": "online", "capabilities": ["manipulation"]}

    @property
    def submissions(self):
        return [
            body
            for method, path, body in self.calls
            if path.endswith("/operations") and method == "POST"
        ]


async def setup(*, scope="a", preflight=None):
    clock = Clock()
    w = world(clock_resolver=clock)

    def fact(identifier, subject, predicate, value, *, scope="a"):
        return replace(
            claim(identifier, value, scope=scope, valid_for_ns=10_000_000_000),
            subject=EntityRef(subject),
            predicate=predicate,
        )

    def pose(x):
        return {"xyz": [x, 0, 0.05], "quaternion": [1, 0, 0, 0], "frame_id": "world"}

    await w.observe(
        observation(),
        claims=(
            fact("category", "cube", "category", "cube"),
            fact("color", "cube", "color", "red"),
            fact("target", "cube", "pose", pose(0)),
            fact("basket-a", "basket", "pose", pose(0.1)),
            fact("basket-b", "basket", "pose", pose(0.2), scope="b"),
            fact("holding", "robot", "holding", EntityRef("cube")),
        ),
    )
    source = await w.snapshot()

    async def ready(snapshot, arguments):
        return True

    transport = Transport()
    bridge = Mfr3duoBridge(
        w,
        transport,
        "robot",
        scope=scope,
        clock_resolver=clock,
        pre_dispatch=preflight or ready,
    )
    tools = {t.definition.name: t for t in await bridge.tools()}
    return w, clock, source, transport, bridge, tools


def context(snapshot, *, policy, message="turn"):
    root = RuntimeRunExecutionContext.create_root(
        root_run_id=message,
        cancellation=RuntimeCancellation(),
        deadline=None,
        budget=ExecutionBudgetConfig(),
        settlement_timeout=1,
        cleanup_timeout=1,
        max_execution_records=64,
        max_record_evidence_bytes=8192,
    )
    policy.bind_run(root)
    policy.begin_turn(message, message)
    executor = ToolExecutor(registry=ToolRegistry())
    parent = root.tool_context(executor, "session")
    parent.bind_model_turn(message, snapshot)
    child = parent.child_tool_context(
        ToolCall("action", "pick_object"), executor, "session"
    )
    return root, ToolContext(
        message, "session", child.cancellation, child, message, snapshot
    )


def arguments(snapshot, *, place=False):
    roles = [("target_pose", "cube", "pose")]
    if place:
        roles.extend(
            (("holding", "robot", "holding"), ("receptacle_pose", "basket", "pose"))
        )
    return freeze_json_object(
        {
            "target_entity": "cube",
            **({"receptacle_entity": "basket"} if place else {}),
            "decision_evidence": decision_evidence(
                snapshot, "cube", roles=roles, scope="a"
            ),
        }
    )


def test_place_rejects_cross_scope_evidence_and_dispatches_exact_scoped_pose():
    async def check():
        _, _, source, transport, bridge, tools = await setup(scope="b")
        root, ctx = context(source, policy=bridge.policy)
        try:
            with pytest.raises(ToolExecutionFailure) as error:
                await tools["place_object"].execute(arguments(source, place=True), ctx)
            assert error.value.error.code == "decision_scope_mismatch"
            assert not transport.submissions
            bridge.scope = "a"
            root2, ctx2 = context(source, policy=bridge.policy, message="next")
            try:
                await tools["place_object"].execute(arguments(source, place=True), ctx2)
                assert transport.submissions[0]["pose"]["x"] == 0.1
            finally:
                await root2.finalize()
        finally:
            await root.finalize()

    asyncio.run(check())


def test_shared_bridge_admits_only_one_run_during_preflight():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def preflight(snapshot, arguments):
            entered.set()
            await release.wait()
            return True

        _, _, source, transport, bridge, tools = await setup(preflight=preflight)
        root1, ctx1 = context(source, policy=bridge.policy, message="one")
        root2, ctx2 = context(source, policy=bridge.policy, message="two")
        first = asyncio.create_task(
            tools["pick_object"].execute(arguments(source), ctx1)
        )
        await entered.wait()
        try:
            with pytest.raises(ToolExecutionFailure) as error:
                await tools["pick_object"].execute(arguments(source), ctx2)
            assert error.value.error.code == "physical_task_unresolved"
            release.set()
            await first
            assert len(transport.submissions) == 1 and bridge.pending == "op_1"
        finally:
            release.set()
            await first
            await root1.finalize()
            await root2.finalize()

    asyncio.run(check())


@pytest.mark.parametrize("boundary", ["preflight", "backend_info"])
@pytest.mark.parametrize("age", [2_000_000_000, 20_000_000_000])
def test_dispatch_rechecks_pose_age_and_world_after_awaits(boundary, age):
    async def check():
        w, clock, source, transport, bridge, tools = await setup()

        async def advance(*args):
            clock.times["sim:1"] += age
            return True

        if boundary == "preflight":
            bridge.pre_dispatch = advance
        else:
            transport.info_hook = advance
        root, ctx = context(source, policy=bridge.policy)
        try:
            with pytest.raises(ToolExecutionFailure) as error:
                await tools["pick_object"].execute(arguments(source), ctx)
            assert error.value.error.code == (
                "pose_stale" if age < 10_000_000_000 else "decision_evidence_uncertain"
            )
            status = (
                (await w.snapshot())
                .lookup(WorldEntryKey(EntityRef("cube"), "pose", "a"))
                .status
            )
            assert status is (
                WorldStatus.KNOWN if age < 10_000_000_000 else WorldStatus.STALE
            )
            assert not transport.submissions and bridge.pending is None
        finally:
            await root.finalize()

    asyncio.run(check())


def test_stop_waits_for_submission_receipt_before_clearing_pending():
    async def check():
        _, _, source, transport, bridge, tools = await setup()
        entered, release = asyncio.Event(), asyncio.Event()

        async def submit():
            entered.set()
            await release.wait()

        transport.submit_hook = submit
        root, ctx = context(source, policy=bridge.policy)
        task = asyncio.create_task(tools["pick_object"].execute(arguments(source), ctx))
        await entered.wait()
        stop = asyncio.create_task(bridge.stop())
        await asyncio.sleep(0)
        assert not any(path.endswith("/stop") for _, path, _ in transport.calls)
        release.set()
        try:
            await task
            await stop
            assert bridge.pending is None and not bridge.uncertain
        finally:
            await root.finalize()

    asyncio.run(check())


@pytest.mark.parametrize(
    "status,error,cleared",
    [
        ("succeeded", None, True),
        ("cancelled", {"code": "run_cancelled"}, True),
        ("failed", {"code": "robot_recovery_required"}, False),
        ("failed", {"code": "internal_error"}, False),
        ("cancelled", {"code": "internal_error"}, False),
    ],
)
def test_actual_operation_terminal_contract(status, error, cleared):
    async def check():
        _, _, _, transport, bridge, _ = await setup()
        bridge.pending = "op_1"
        transport.record.update(
            status=status, error=error, finished_at="2026-10-11T00:00:00Z"
        )
        result = await bridge.task_status()
        assert (bridge.pending is None) is cleared
        assert ("completion_barrier" in result) is cleared

    asyncio.run(check())


@pytest.mark.parametrize(
    "field,value",
    [("operation_id", "op_other"), ("robot_id", "other"), ("status", "invented")],
)
def test_status_identity_is_validated_before_reconciliation(field, value):
    async def check():
        _, _, _, transport, bridge, _ = await setup()
        bridge.pending = "op_1"
        transport.record.update(status="succeeded", finished_at="2026-10-11T00:00:00Z")
        transport.record[field] = value
        with pytest.raises(ToolExecutionFailure, match="invalid_operation_status"):
            await bridge.task_status()
        assert bridge.pending == "op_1"

    asyncio.run(check())


def test_preflight_cancellation_releases_admission_without_submitting():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def preflight(snapshot, arguments):
            entered.set()
            await release.wait()
            return True

        _, _, source, transport, bridge, tools = await setup(preflight=preflight)
        root, ctx = context(source, policy=bridge.policy)
        task = asyncio.create_task(tools["pick_object"].execute(arguments(source), ctx))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not transport.submissions and bridge.pending is None
        assert not bridge.uncertain and not bridge._admission.locked()
        await root.finalize()

    asyncio.run(check())


def test_run_finalization_during_preflight_blocks_dispatch():
    async def check():
        _, _, source, transport, bridge, tools = await setup()
        root, ctx = context(source, policy=bridge.policy)

        async def preflight(snapshot, arguments):
            await root.finalize()
            return True

        bridge.pre_dispatch = preflight
        with pytest.raises(ToolExecutionFailure) as error:
            await tools["pick_object"].execute(arguments(source), ctx)
        assert error.value.error.code == "fresh_decision_required"
        assert not transport.submissions and bridge.pending is None

    asyncio.run(check())


@pytest.mark.parametrize("end", ["release", "close_tool", "finalize"])
def test_host_closed_model_turn_cannot_reacquire_action_slot(end) -> None:
    async def check():
        from tests.runtime.test_nested_execution import _tree
        from examples.embodied.contracts import PhysicalPolicy

        tree = _tree()
        root = RuntimeRunExecutionContext(tree.root_scope)
        policy = PhysicalPolicy(frozenset(("physical",)))
        policy.bind_run(root)
        policy.begin_turn("root", "turn")
        parent = root.tool_context(object(), "session")
        parent.bind_model_turn("turn", None)
        child = parent.child_tool_context(
            ToolCall("action", "physical"), object(), "session"
        )
        ctx = ToolContext("root", "session", child.cancellation, child, "turn")
        assert policy.claim_turn_action(ctx) and policy.owns_turn_action(ctx)
        if end == "release":
            policy.begin_turn("root", "next")
            assert tree.root_scope.state is ExecutionScopeState.OPEN
        elif end == "close_tool":
            await child.close_tool_scope()
        else:
            await root.finalize()
        assert not policy.claim_turn_action(ctx) and not policy.owns_turn_action(ctx)
        if end == "release":
            # Rebinding a retained nested context must not reactivate its old turn.
            child.bind_model_turn("turn", None)
            assert not policy.claim_turn_action(ctx)
            parent.bind_model_turn("next", None)
            fresh = parent.child_tool_context(
                ToolCall("fresh", "physical"), object(), "session"
            )
            fresh_ctx = ToolContext(
                "root", "session", fresh.cancellation, fresh, "next"
            )
            assert policy.claim_turn_action(fresh_ctx)
        await root.finalize()
        assert not policy._turn_actions and not policy._runs

    asyncio.run(check())


@pytest.mark.parametrize("release_turn", [False, True])
@pytest.mark.parametrize("close_tool", [False, True])
def test_host_turn_action_is_invalidated_but_retained_until_settlement(
    release_turn, close_tool
) -> None:
    async def check():
        from tests.runtime.test_nested_execution import _tree
        from examples.embodied.contracts import PhysicalPolicy

        tree = _tree(settlement_timeout=1)
        root = RuntimeRunExecutionContext(tree.root_scope)
        policy = PhysicalPolicy(frozenset(("physical",)))
        policy.bind_run(root)
        policy.begin_turn("root", "turn")
        parent = root.tool_context(object(), "session")
        parent.bind_model_turn("turn", None)
        child = parent.child_tool_context(
            ToolCall("action", "physical"), object(), "session"
        )
        ctx = ToolContext("root", "session", child.cancellation, child, "turn")
        assert policy.claim_turn_action(ctx)
        entered, release = asyncio.Event(), asyncio.Event()

        class Handler:
            async def settle(self):
                entered.set()
                await release.wait()

            async def force_settle(self):
                raise AssertionError("Settlement must finish normally.")

        async def invoke():
            async with child.settlement_barrier(handler=Handler()):
                pass

        invocation = asyncio.create_task(invoke())
        await entered.wait()
        if release_turn:
            policy.begin_turn("root", "next")
            assert not policy.owns_turn_action(ctx)
        closing = asyncio.create_task(
            child.close_tool_scope() if close_tool else root.finalize()
        )
        await asyncio.sleep(0)
        assert not closing.done()
        assert release_turn or policy._turn_actions == {
            ("root", "turn"): child.lineage.scope_id
        }
        assert not policy.claim_turn_action(ctx) and not policy.owns_turn_action(ctx)
        release.set()
        await invocation
        await closing
        if close_tool:
            await root.finalize()
        assert not policy._turn_actions and not policy._runs

    asyncio.run(check())


def test_physical_capable_wrapper_rejects_overlapping_nested_execution():
    async def check():
        from examples.embodied.contracts import PhysicalPolicy
        from roboagent.runtime.execution import RuntimeRunExecutionContext
        from tests.runtime.test_nested_execution import _tree
        from roboagent.tool import ToolContext, ToolExecutor, ToolJsonContent
        from tests.integration.test_decision_context_v11 import tool

        started, release = asyncio.Event(), asyncio.Event()
        results = []

        async def leaf(args, context):
            started.set()
            await release.wait()
            return ToolJsonContent("ok")

        async def wrapper(args, context):
            first = asyncio.create_task(
                context.execution.execute_nested_tool("leaf", {})
            )
            await started.wait()
            results.append(await context.execution.execute_nested_tool("leaf", {}))
            release.set()
            results.append(await first)
            return ToolJsonContent("ok")

        executor = ToolExecutor(
            registry=ToolRegistry((tool("wrapper", wrapper), tool("leaf", leaf))),
            policy=PhysicalPolicy(frozenset(), frozenset(("wrapper",))),
        )
        runtime = RuntimeRunExecutionContext(_tree().root_scope)
        execution = runtime.tool_context(executor, "session")
        context = ToolContext(
            "root", "session", execution.cancellation, execution, "model"
        )
        await executor.execute((ToolCall("wrapper", "wrapper"),), context)
        assert results[0].error.code == "rejected"
        assert results[1].error is None
        assert not executor.policy._nested_active
        await runtime.finalize()

    asyncio.run(check())
