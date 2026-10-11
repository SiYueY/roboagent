"""CPU-only MuJoCo observation/action/independent-feedback composition.

The actuator drives a constrained carriage. This is a real physics integration,
not a grasping backend or an mfr3duo robot driver. No camera, cloud model or ROS
Python stack is required. All environment state remains owned by World.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from uuid import uuid4

from roboagent import Agent
from roboagent.context import ContextDataSegment, ModelContext
from roboagent.message import (
    AssistantMessage,
    FrozenJsonObject,
    FrozenJsonArray,
    ToolCall,
    UserMessage,
    freeze_json,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolJsonContent,
    ToolRegistry,
)
from roboagent.world import (
    Claim,
    ClaimKind,
    ClaimRef,
    EntityRef,
    EvidenceRef,
    Observation,
    ObservationRef,
    ObservationTime,
    World,
    WorldStatus,
    WorldReceipt,
    create_world_tools,
)

SCENE = """<mujoco>
  <option timestep="0.002" gravity="0 0 0"/>
  <worldbody>
    <body name="carriage">
      <joint name="slide" type="slide" axis="1 0 0" limited="true"
             range="-0.5 0.5" damping="4"/>
      <geom name="carriage_visual" type="box" size="0.03 0.03 0.03" mass="1" rgba="1 0 0 1"/>
    </body>
  </worldbody>
  <actuator><position joint="slide" kp="100" ctrlrange="-0.5 0.5"
                      ctrllimited="true" forcerange="-10 10" forcelimited="true"/></actuator>
</mujoco>"""


class MuJoCoAdapter:
    """Trusted Host adapter binding source/scope and simulation clock identity."""

    def __init__(self) -> None:
        import mujoco

        self._mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_string(SCENE)
        self.data = mujoco.MjData(self.model)
        self.clock_id = "mujoco:" + uuid4().hex
        self.world = World(
            "carriage-demo",
            clock_resolver=self,
            allowed_sources=("mujoco/state",),
            allowed_scopes=("scene",),
        )
        self._sequence = 0
        self._body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, "carriage"
        )
        self._geom_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, "carriage_visual"
        )
        mujoco.mj_forward(self.model, self.data)

    def now(self, clock_id: str) -> ObservationTime | None:
        return (
            ObservationTime(clock_id, int(self.data.time * 1e9))
            if clock_id == self.clock_id
            else None
        )

    def reset(self) -> None:
        self._mujoco.mj_resetData(self.model, self.data)
        self._mujoco.mj_forward(self.model, self.data)
        self.clock_id = "mujoco:" + uuid4().hex

    async def observe(self) -> WorldReceipt:
        self._mujoco.mj_forward(self.model, self.data)
        sampled = self.now(self.clock_id)
        assert sampled is not None
        received = datetime.now(timezone.utc)
        self._sequence += 1
        ref = ObservationRef("mujoco/state", str(self._sequence))
        # Copy out of mutable MuJoCo arrays before constructing immutable data.
        pose = {
            "frame": "world",
            "xyz": [float(v) for v in self.data.body("carriage").xpos],
        }
        rgba = [float(value) for value in self.model.geom_rgba[self._geom_id]]
        geom_type = int(self.model.geom_type[self._geom_id])
        category = self._mujoco.mjtGeom(geom_type).name.removeprefix("mjGEOM_").lower()
        color = _color_from_rgba(rgba)
        telemetry = {
            "body_name": self._mujoco.mj_id2name(
                self.model, self._mujoco.mjtObj.mjOBJ_BODY, self._body_id
            ),
            "geom_type": geom_type,
            "geom_rgba": rgba,
            "pose": pose,
            "joint_position": float(self.data.qpos[0]),
            "joint_velocity": float(self.data.qvel[0]),
        }
        observation = Observation(
            ref, "robot_state", sampled, received, freeze_json(telemetry), "scene"
        )
        claims = tuple(
            Claim(
                ClaimRef(f"{self._sequence}:{predicate}"),
                EntityRef("carriage"),
                predicate,
                freeze_json(value),
                ClaimKind.MEASUREMENT,
                ref.source,
                "scene",
                (EvidenceRef("observation", ref.source, ref.id),),
                received,
                sampled,
                valid_for_ns=5_000_000_000,
            )
            for predicate, value in (
                ("pose", pose),
                ("category", category),
                ("color", color),
            )
        )
        return await self.world.observe(observation, claims=claims)

    def tools(self) -> tuple[Tool, ...]:
        async def observe(arguments, context):
            context.cancellation.raise_if_cancelled()
            receipt = await self.observe()
            assert receipt.observation_ref is not None
            return ToolJsonContent(
                freeze_json(
                    {
                        "status": "committed",
                        "world_revision": receipt.revision,
                        "observation_ref": {
                            "source": receipt.observation_ref.source,
                            "id": receipt.observation_ref.id,
                        },
                    }
                )
            )

        async def drive(arguments, context):
            target = arguments["target_x"]
            expected = arguments["expected_x"]
            answer = await self.world.query(EntityRef("carriage"), "pose")
            if answer.status is not WorldStatus.KNOWN or not isinstance(
                answer.value, FrozenJsonObject
            ):
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "target_unavailable", "A fresh target observation is required."
                    )
                )
            pose = answer.value
            xyz = pose.get("xyz")
            if (
                not isinstance(xyz, FrozenJsonArray)
                or len(xyz) != 3
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in xyz
                )
            ):
                raise ToolExecutionFailure(
                    ToolErrorInfo("invalid_pose", "Pose geometry is unavailable.")
                )
            # Refresh derived geometry immediately before the irreversible command.
            self._mujoco.mj_forward(self.model, self.data)
            current_xyz = tuple(
                float(value) for value in self.data.body("carriage").xpos
            )
            if not all(
                math.isfinite(value)
                for value in (
                    *current_xyz,
                    float(self.data.qpos[0]),
                    float(self.data.qvel[0]),
                )
            ):
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "robot_state_unavailable", "Robot state is not finite."
                    )
                )
            # Revalidate the dependency, not the global revision. Geometry comes
            # from the sampled simulator state, never invented language XYZ.
            if (
                pose.get("frame") != "world"
                or abs(xyz[0] - expected) > 0.001
                or any(
                    abs(current - observed) > 0.001
                    for current, observed in zip(current_xyz, xyz, strict=True)
                )
            ):
                raise ToolExecutionFailure(
                    ToolErrorInfo("target_moved", "Target pose changed; observe again.")
                )
            if not math.isfinite(target) or not -0.4 <= target <= 0.4:
                raise ToolExecutionFailure(
                    ToolErrorInfo(
                        "unsafe_target",
                        "Target is outside the example's safe joint range.",
                    )
                )
            context.cancellation.raise_if_cancelled()
            self.data.ctrl[0] = target
            try:
                for step in range(2000):
                    context.cancellation.raise_if_cancelled()
                    self._mujoco.mj_step(self.model, self.data)
                    if step % 50 == 0:
                        await asyncio.sleep(0)
            finally:
                # Host stops issuing motion on cancellation; this is not a
                # claim that Run cancellation has verified physical standstill.
                self.data.ctrl[0] = self.data.qpos[0]
            return ToolJsonContent(
                freeze_json(
                    {
                        "status": "controller_execution_finished",
                        "physical_goal_verified": False,
                    }
                )
            )

        return (
            *create_world_tools(self.world),
            Tool(
                ToolDefinition(
                    "observe_state",
                    "Acquire independent MuJoCo state evidence and commit claims before returning.",
                    FrozenJsonObject(
                        {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        }
                    ),
                ),
                observe,
            ),
            Tool(
                ToolDefinition(
                    "drive_carriage",
                    "Drive the carriage after checking the fresh observed world-frame pose and safe joint range.",
                    FrozenJsonObject(
                        {
                            "type": "object",
                            "properties": {
                                "target_x": {
                                    "type": "number",
                                    "minimum": -0.4,
                                    "maximum": 0.4,
                                },
                                "expected_x": {"type": "number"},
                            },
                            "required": ["target_x", "expected_x"],
                            "additionalProperties": False,
                            "x-world-entities": ["carriage"],
                        }
                    ),
                ),
                drive,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
                timeout=5,
            ),
        )


def _color_from_rgba(rgba: list[float]) -> str | dict[str, list[float]]:
    """Deterministic labels for the example's simple primary-color palette.

    Unmatched colors retain measured RGBA rather than inventing a semantic label.
    This maps simulator metadata; it is not a vision/perception service.
    """
    palette = {
        "red": (1.0, 0.0, 0.0),
        "green": (0.0, 1.0, 0.0),
        "blue": (0.0, 0.0, 1.0),
        "white": (1.0, 1.0, 1.0),
        "black": (0.0, 0.0, 0.0),
        "yellow": (1.0, 1.0, 0.0),
    }
    for name, rgb in palette.items():
        if all(
            abs(actual - expected) <= 0.01
            for actual, expected in zip(rgba[:3], rgb, strict=True)
        ):
            return name
    return {"rgba": rgba}


class DemoModel:
    """Deterministic Model fixture; all execution uses canonical Agent/Tool."""

    capabilities = ModelCapabilities(tool_calling=True)

    def __init__(self) -> None:
        self.contexts: list[ModelContext] = []

    async def stream(self, context, settings=None):
        import json

        self.contexts.append(context)
        entries = json.loads(
            next(
                segment.text
                for segment in context.segments
                if isinstance(segment, ContextDataSegment)
            )
        )
        pose = next(
            entry
            for entry in entries
            if entry["subject"]["id"] == "carriage" and entry["predicate"] == "pose"
        )
        color = next(
            (
                entry
                for entry in entries
                if entry["subject"]["id"] == "carriage"
                and entry["predicate"] == "color"
            ),
            None,
        )
        if len(self.contexts) == 1 and (
            color is None
            or color["status"] != "known"
            or color["value"] != "red"
            or pose["status"] != "known"
        ):
            message = AssistantMessage(
                "The fresh red carriage target is unavailable; no motion was requested."
            )
        elif len(self.contexts) == 1:
            message = AssistantMessage(
                tool_calls=(
                    ToolCall(
                        "drive",
                        "drive_carriage",
                        FrozenJsonObject(
                            {"expected_x": pose["value"]["xyz"][0], "target_x": 0.2}
                        ),
                    ),
                )
            )
        elif len(self.contexts) == 2:
            message = AssistantMessage(
                tool_calls=(ToolCall("observe", "observe_state"),)
            )
        else:
            verified = (
                pose["status"] == "known" and abs(pose["value"]["xyz"][0] - 0.2) < 0.005
            )
            message = AssistantMessage(
                "Physical goal verified from independent evidence."
                if verified
                else "Physical goal remains unverified."
            )
        yield ResponseStarted("demo", 0)
        sequence = 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        yield ResponseCompleted(
            sequence,
            ModelResponse(
                message,
                FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP,
            ),
        )


async def main() -> None:
    adapter = MuJoCoAdapter()
    await adapter.observe()  # World ingestion does not require a Session or Run.
    agent = Agent(
        DemoModel(), world=adapter.world, tool_registry=ToolRegistry(adapter.tools())
    )
    session = agent.new_session()
    try:
        result = await session.run(
            UserMessage("Move the red carriage to x=0.2 and verify with new evidence.")
        )
        print(result.status.value, result.output)
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
