"""World V1.1 source decisions and real contact-based tabletop acceptance."""

import asyncio
import os
from dataclasses import replace

import pytest

pytest.importorskip("mujoco")

from examples.embodied.contracts import decision_evidence, validate_decision
from examples.embodied.reference import main
from examples.embodied.tabletop import Tabletop
from roboagent.message import ToolCall
from roboagent.runtime import RuntimeCancellation, RunStatus
from roboagent.runtime.execution import RuntimeRunExecutionContext
from roboagent.runtime._execution.budget import ExecutionBudgetConfig
from roboagent.tool import ToolContext, ToolExecutionFailure, ToolExecutor, ToolRegistry
from roboagent.world import EntityRef, WorldEntryKey, WorldStatus


def execution_context(snapshot, *, policy, message="model-1"):
    from uuid import uuid4

    run_id = uuid4().hex
    runtime = RuntimeRunExecutionContext.create_root(
        root_run_id=run_id,
        cancellation=RuntimeCancellation(),
        deadline=None,
        budget=ExecutionBudgetConfig(),
        settlement_timeout=1,
        cleanup_timeout=1,
        max_execution_records=64,
        max_record_evidence_bytes=8192,
    )
    policy.bind_run(runtime)
    policy.begin_turn(run_id, message)
    executor = ToolExecutor(registry=ToolRegistry())
    parent = runtime.tool_context(executor, "session")
    parent.bind_model_turn(message, snapshot)
    child = parent.child_tool_context(
        ToolCall("action", "pick_object"), executor, "session"
    )
    return ToolContext(run_id, "session", child.cancellation, child, message, snapshot)


def arguments(snapshot):
    return {
        "target_entity": "cube_red",
        "decision_evidence": decision_evidence(
            snapshot, "cube_red", roles=(("target_pose", "cube_red", "pose"),)
        ),
    }


def test_real_physics_pick_release_inside_and_stable():
    async def check():
        result, table = await main()
        assert result.status is RunStatus.COMPLETED
        assert not table.policy._runs and not table.policy._turn_actions
        assert (
            result.output.content[0].text
            == "Placement verified from post-action evidence."
        )
        assert table.inside("cube_red") and not table.holding("cube_red")
        assert table.pose("cube_blue")["xyz"][0] < 0
        snapshot = await table.world.snapshot()
        assert (
            snapshot.lookup(
                WorldEntryKey(EntityRef("cube_red"), "stable", "workspace")
            ).value
            is True
        )
        assert table.barrier is not None
        assert (
            snapshot.lookup(
                WorldEntryKey(EntityRef("cube_red"), "inside", "workspace")
            ).valid_at.nanoseconds
            > table.barrier.nanoseconds
        )

    asyncio.run(check())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "decision_evidence_missing"),
        ("readiness", "decision_evidence_incomplete"),
        ("wrong_world", "world_mismatch"),
        ("source_stale", "decision_source_not_ready"),
        ("wrong_ref", "decision_evidence_invalid"),
    ],
)
def test_invalid_source_decisions_fail_before_physics(fault, code):
    async def check():
        table = Tabletop()
        await table.observe()
        snapshot = await table.world.snapshot()
        args = arguments(snapshot)
        if fault == "missing":
            del args["decision_evidence"]
        elif fault == "readiness":
            args["decision_evidence"]["readiness"] = []
        elif fault == "wrong_world":
            snapshot = replace(snapshot, world_id="foreign")
        elif fault == "wrong_ref":
            args["decision_evidence"]["readiness"][0]["claim_refs"] = ["absent"]
        elif fault == "source_stale":
            snapshot = replace(
                snapshot,
                entries=tuple(
                    replace(e, status=WorldStatus.STALE, value=None)
                    if e.subject.id == "cube_red" and e.predicate == "pose"
                    else e
                    for e in snapshot.entries
                ),
            )
        context = execution_context(snapshot, policy=table.policy)
        before = table.data.time
        with pytest.raises(ToolExecutionFailure) as error:
            await table.action(args, context)
        assert error.value.error.code == code
        assert table.data.time == before
        assert table.active_task is None

    asyncio.run(check())


def test_failed_attempt_consumes_slot_and_same_fact_refresh_is_compatible():
    async def check():
        table = Tabletop()
        await table.observe()
        source = await table.world.snapshot()
        context = execution_context(source, policy=table.policy)
        args = arguments(source)
        # A newer observation replaces ClaimRefs without changing the decision facts.
        await table.observe()
        await validate_decision(
            table.world,
            context,
            args,
            {"target_pose": ("cube_red", "pose")},
            table.tolerance,
            scope="workspace",
            clock_resolver=table,
            policy=table.policy,
        )
        with pytest.raises(ToolExecutionFailure) as error:
            await validate_decision(
                table.world,
                context,
                args,
                {"target_pose": ("cube_red", "pose")},
                table.tolerance,
                scope="workspace",
                clock_resolver=table,
                policy=table.policy,
            )
        assert error.value.error.code == "fresh_decision_required"

    asyncio.run(check())


def test_direct_execution_and_new_observation_refs_are_rejected():
    async def check():
        table = Tabletop()
        await table.observe()
        source = await table.world.snapshot()
        with pytest.raises(ToolExecutionFailure) as error:
            await table.action(
                arguments(source), ToolContext("run", "session", RuntimeCancellation())
            )
        assert error.value.error.code == "decision_context_missing"
        await table.observe()
        newer = await table.world.snapshot()
        with pytest.raises(ToolExecutionFailure) as error:
            await table.action(
                arguments(newer), execution_context(source, policy=table.policy)
            )
        assert error.value.error.code == "decision_evidence_invalid"

    asyncio.run(check())


def test_canonical_nested_attempt_blocks_later_direct_action():
    async def check():
        from dataclasses import replace
        from examples.embodied.contracts import PhysicalPolicy
        from roboagent import Agent
        from roboagent.message import AssistantMessage, UserMessage
        from roboagent.tool import Tool, ToolDefinition, ToolJsonContent
        from tests.support.models import ScriptedModel

        table = Tabletop()
        await table.observe()
        source = await table.world.snapshot()
        args = arguments(source)
        errors = []

        async def wrapper(arguments, context):
            invalid = {
                **args,
                "decision_evidence": {**args["decision_evidence"], "readiness": []},
            }
            nested = await context.execution.execute_nested_tool("pick_object", invalid)
            errors.append(nested.error.code)
            return ToolJsonContent("nested attempt finished")

        wrapper_tool = Tool(
            ToolDefinition(
                "wrapper", "physical wrapper", {"type": "object", "properties": {}}
            ),
            wrapper,
        )
        model = ScriptedModel(
            AssistantMessage(
                tool_calls=(
                    ToolCall("w", "wrapper"),
                    ToolCall("p", "pick_object", args),
                )
            ),
            AssistantMessage("done"),
        )
        model.capabilities = replace(model.capabilities, parallel_tool_calls=True)
        table.policy = PhysicalPolicy(
            frozenset(("pick_object", "place_object")), frozenset(("wrapper",))
        )
        agent = Agent(
            model,
            world=table.world,
            tool_registry=ToolRegistry((*table.tools(), wrapper_tool)),
            tool_policy=table.policy,
            hooks=(table.policy,),
        )
        before = table.data.time
        session = agent.new_session()
        try:
            result = await session.run(UserMessage("go"))
        finally:
            await session.close()
        assert result.status is RunStatus.COMPLETED
        assert errors == ["decision_evidence_incomplete"]
        assert any(
            effect.error is not None and effect.error.code == "fresh_decision_required"
            for effect in result.effects
        )
        assert table.data.time == before

    asyncio.run(check())


@pytest.mark.skipif(
    os.environ.get("ROBOAGENT_EMBODIED_LIVE") != "1",
    reason="Explicit cloud credentials/config and live opt-in required",
)
def test_live_model_grounding_and_physical_verification():
    async def check():
        from roboagent.config import load_app_config
        from roboagent.model import create_model

        model = create_model(registry=load_app_config().to_model_registry())
        result, table = await main(model)
        assert result.status is RunStatus.COMPLETED
        assert table.inside("cube_red") and not table.holding("cube_red")
        snapshot = await table.world.snapshot()
        assert (
            snapshot.lookup(
                WorldEntryKey(EntityRef("cube_red"), "stable", "workspace")
            ).value
            is True
        )

    asyncio.run(check())


def test_controller_completion_does_not_prove_grasp_success():
    async def check():
        table = Tabletop()
        for name in ("red", "left_finger", "right_finger"):
            table.model.geom(name).friction[:] = 0
        await table.observe()
        source = await table.world.snapshot()
        result = await table.action(
            arguments(source), execution_context(source, policy=table.policy)
        )
        assert result.value["status"] == "completed"
        assert result.value["physical_verified"] is False
        await table.observe(verification=True)
        holding = (await table.world.snapshot()).lookup(
            WorldEntryKey(EntityRef("robot"), "holding", "workspace")
        )
        assert holding.status is WorldStatus.KNOWN and holding.value is None
        assert not table.holding("cube_red")

    asyncio.run(check())


def test_cancellation_preserves_world_and_stops_the_active_task():
    async def check():
        table = Tabletop()
        await table.observe()
        source = await table.world.snapshot()
        context = execution_context(source, policy=table.policy)
        action = asyncio.create_task(table.action(arguments(source), context))
        await asyncio.sleep(0)
        assert table.active_task is not None
        context.cancellation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
        assert table.active_task is None
        assert "cancelled" in table.task_states.values()
        assert (await table.world.snapshot()).revision == source.revision

    asyncio.run(check())


def test_bridge_ack_loss_records_unknown_and_blocks_automatic_retry():
    async def check():
        from examples.embodied.mfr3duo_bridge import Mfr3duoBridge
        from roboagent.tool import ToolEffectUnknown

        table = Tabletop()
        await table.observe()
        source = await table.world.snapshot()

        class Disconnect:
            posts = 0

            async def request(self, method, path, body=None, **kwargs):
                if method == "POST":
                    self.posts += 1
                    raise ConnectionError("ACK delivery lost")
                return {"connection": "online", "capabilities": ["manipulation"]}

        transport = Disconnect()

        async def preflight(snapshot, arguments):
            return True  # Level 0 transport fault fixture; no hardware dispatch.

        bridge = Mfr3duoBridge(
            table.world,
            transport,
            "robot",
            clock_resolver=table,
            pre_dispatch=preflight,
        )
        tools = await bridge.tools()
        pick = next(t for t in tools if t.definition.name == "pick_object")
        from roboagent.message import freeze_json_object

        with pytest.raises(ToolEffectUnknown):
            await pick.execute(
                freeze_json_object(arguments(source)),
                execution_context(source, policy=bridge.policy),
            )
        assert bridge.pending is not None and bridge.uncertain
        with pytest.raises(ToolExecutionFailure) as error:
            await pick.execute(
                freeze_json_object(arguments(source)),
                execution_context(source, policy=bridge.policy, message="new-turn"),
            )
        assert error.value.error.code == "physical_task_unresolved"
        assert transport.posts == 1

    asyncio.run(check())


def test_ambiguous_red_targets_never_dispatch_a_physical_action():
    async def check():
        from examples.embodied.reference import TabletopModel
        from roboagent import Agent
        from roboagent.message import UserMessage
        from roboagent.world import create_world_tools

        table = Tabletop()
        table.model.geom("blue").rgba[:] = [1, 0, 0, 1]
        await table.observe()
        before = table.data.time
        agent = Agent(
            TabletopModel(),
            world=table.world,
            tool_registry=ToolRegistry(
                (*create_world_tools(table.world), *table.tools())
            ),
            tool_policy=table.policy,
            hooks=(table.policy,),
        )
        session = agent.new_session()
        try:
            result = await session.run(UserMessage("Put the red cube in the basket."))
        finally:
            await session.close()
        assert result.status is RunStatus.COMPLETED
        assert (
            result.output.content[0].text
            == "Target requires observation or clarification."
        )
        assert table.data.time == before and not table.task_states

    asyncio.run(check())


def test_object_observation_updates_only_requested_entity_and_robot_state():
    async def check():
        table = Tabletop()
        await table.observe()
        before = await table.world.snapshot()
        blue_key = WorldEntryKey(EntityRef("cube_blue"), "pose", "workspace")
        receipt = await table.observe(entity=EntityRef("cube_red"))
        assert receipt.changed
        after = await table.world.snapshot()
        assert after.lookup(blue_key) == before.lookup(blue_key)
        red_key = WorldEntryKey(EntityRef("cube_red"), "pose", "workspace")
        assert (
            after.lookup(red_key).valid_at.nanoseconds
            > before.lookup(red_key).valid_at.nanoseconds
        )

    asyncio.run(check())
