import asyncio
import json

import pytest

pytest.importorskip("mujoco")

from examples.embodied.mujoco import DemoModel, MuJoCoAdapter
from roboagent import Agent
from roboagent.context import ContextDataSegment
from roboagent.message import FrozenJsonObject, UserMessage
from roboagent.runtime import RunStatus, RuntimeCancellation
from roboagent.tool import ToolContext, ToolExecutionFailure, ToolRegistry
from roboagent.world import EntityRef, WorldStatus


def test_real_cpu_physics_ack_observation_verification_and_pause():
    async def check():
        adapter = MuJoCoAdapter()
        await adapter.observe()
        first = await adapter.world.snapshot()
        await asyncio.sleep(0)
        assert (
            await adapter.world.snapshot()
        ).entries == first.entries  # sim clock paused
        model = DemoModel()
        session = Agent(
            model, world=adapter.world, tool_registry=ToolRegistry(adapter.tools())
        ).new_session()
        result = await session.run(UserMessage("move and verify"))
        assert result.status is RunStatus.COMPLETED
        assert (
            result.output.content[0].text
            == "Physical goal verified from independent evidence."
        )
        data = [
            json.loads(
                next(s.text for s in c.segments if isinstance(s, ContextDataSegment))
            )
            for c in model.contexts
        ]
        positions = [
            next(e["value"]["xyz"][0] for e in entries if e["predicate"] == "pose")
            for entries in data
        ]
        assert positions[0] == positions[1] == 0.0
        assert abs(positions[2] - 0.2) < 0.005
        old_clock = adapter.clock_id
        adapter.reset()
        assert adapter.clock_id != old_clock and adapter.now(old_clock) is None
        await adapter.observe()
        assert (
            await adapter.world.query(EntityRef("carriage"), "pose")
        ).status is WorldStatus.KNOWN
        await session.close()

    asyncio.run(check())


def test_preexecution_target_dependency_validation_and_unrelated_revision():
    async def check():
        adapter = MuJoCoAdapter()
        await adapter.observe()
        tools = {tool.definition.name: tool for tool in adapter.tools()}
        context = ToolContext("run", "session", RuntimeCancellation())
        adapter.data.qpos[0] = 0.1
        adapter.data.time += adapter.model.opt.timestep
        adapter._mujoco.mj_forward(adapter.model, adapter.data)
        with pytest.raises(ToolExecutionFailure) as error:
            await tools["drive_carriage"].execute(
                FrozenJsonObject({"expected_x": 0, "target_x": 0.2}), context
            )
        assert error.value.error.code == "target_moved"
        assert adapter.data.ctrl[0] == 0
        await adapter.observe()
        # An unrelated observation commits without changing target dependencies.
        from dataclasses import replace
        from tests.world.test_world import observation
        from roboagent.world import ObservationRef

        await adapter.world.observe(
            replace(
                observation("unrelated"),
                ref=ObservationRef("mujoco/state", "unrelated"),
                scope="scene",
            )
        )
        ack = await tools["drive_carriage"].execute(
            FrozenJsonObject({"expected_x": 0.1, "target_x": 0.2}), context
        )
        assert ack.value["physical_goal_verified"] is False

    asyncio.run(check())


def test_run_cancellation_preserves_cognition_and_requires_stop_verification():
    async def check():
        adapter = MuJoCoAdapter()
        await adapter.observe()
        original = await adapter.world.snapshot()
        model = DemoModel()
        session = Agent(
            model, world=adapter.world, tool_registry=ToolRegistry(adapter.tools())
        ).new_session()
        run = session.start(UserMessage("drive"))
        while float(adapter.data.time) == 0:
            await asyncio.sleep(0)
        run.cancel()
        result = await run.result()
        assert result.status is RunStatus.CANCELLED
        current = await adapter.world.snapshot()
        assert current.revision == original.revision
        assert current.entries == original.entries
        assert adapter.data.ctrl[0] == adapter.data.qpos[0]
        # A stopped command is not a standstill measurement.
        assert float(adapter.data.qvel[0]) != 0
        await session.close()

    asyncio.run(check())


def test_semantic_attributes_follow_sampled_simulator_metadata():
    async def check():
        adapter = MuJoCoAdapter()
        adapter.model.geom_rgba[adapter._geom_id] = [0, 0, 1, 1]
        await adapter.observe()
        assert (
            await adapter.world.query(EntityRef("carriage"), "color")
        ).value == "blue"
        assert (
            await adapter.world.query(EntityRef("carriage"), "category")
        ).value == "box"
        assert not (await adapter.world.find_entities({"color": "red"})).candidates
        assert (
            await adapter.world.find_entities({"color": "blue", "category": "box"})
        ).candidates
        adapter.data.time += adapter.model.opt.timestep
        adapter.model.geom_rgba[adapter._geom_id] = [0.25, 0.25, 0.25, 1]
        await adapter.observe()
        color = (await adapter.world.query(EntityRef("carriage"), "color")).value
        assert color == {"rgba": [0.25, 0.25, 0.25, 1.0]}
        adapter.data.time += adapter.model.opt.timestep
        adapter.model.geom_type[adapter._geom_id] = (
            adapter._mujoco.mjtGeom.mjGEOM_SPHERE
        )
        await adapter.observe()
        assert (
            await adapter.world.query(EntityRef("carriage"), "category")
        ).value == "sphere"

    asyncio.run(check())


def test_semantic_target_mismatch_never_starts_motion():
    async def check():
        adapter = MuJoCoAdapter()
        adapter.model.geom_rgba[adapter._geom_id] = [0, 0, 1, 1]
        await adapter.observe()
        model = DemoModel()
        session = Agent(
            model, world=adapter.world, tool_registry=ToolRegistry(adapter.tools())
        ).new_session()
        try:
            result = await session.run(UserMessage("move the red carriage"))
            assert result.status is RunStatus.COMPLETED
            assert not result.effects
            assert adapter.data.time == 0 and adapter.data.ctrl[0] == 0
            assert "unavailable" in result.output.content[0].text
        finally:
            await session.close()

    asyncio.run(check())


def test_preexecution_refreshes_geometry_instead_of_trusting_cached_xpos():
    async def check():
        adapter = MuJoCoAdapter()
        await adapter.observe()
        adapter.data.qpos[0] = 0.1  # cached xpos still refers to the old state
        drive = next(
            tool for tool in adapter.tools() if tool.definition.name == "drive_carriage"
        )
        with pytest.raises(ToolExecutionFailure) as error:
            await drive.execute(
                FrozenJsonObject({"expected_x": 0, "target_x": 0.2}),
                ToolContext("run", "session", RuntimeCancellation()),
            )
        assert error.value.error.code == "target_moved"
        assert adapter.data.ctrl[0] == 0

    asyncio.run(check())
