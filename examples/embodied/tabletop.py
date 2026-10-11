"""CPU MuJoCo reference with actuator-driven grasp and independent observations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
import math
from uuid import uuid4

from roboagent.message import JsonValue, freeze_json, freeze_json_object
from roboagent.tool import Tool, ToolDefinition, ToolEffectKind, ToolJsonContent
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
)
from .contracts import (
    PoseTolerance,
    reject,
    validate_decision,
    decision_evidence_schema,
)

SCENE = """<mujoco model="tabletop">
<option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>
<default><geom friction="3 0.05 0.01" solref="0.005 1"/><joint damping="3"/>
<position kp="500" kv="30"/></default>
<worldbody>
<light pos="0 0 1"/>
<camera name="front" pos="0 -0.8 0.65" xyaxes="1 0 0 0 0.6 0.8"/>
<geom name="table" type="box" pos="0 0 0.02" size="0.35 0.25 0.02"/>
<body name="basket" pos="0.18 0 0.045">
<geom name="basket_floor" type="box" size="0.075 0.075 0.005"/>
<geom type="box" pos="-0.07 0 0.035" size="0.005 0.075 0.035"/>
<geom type="box" pos="0.07 0 0.035" size="0.005 0.075 0.035"/>
<geom type="box" pos="0 -0.07 0.035" size="0.065 0.005 0.035"/>
<geom type="box" pos="0 0.07 0.035" size="0.065 0.005 0.035"/>
</body>
<body name="cube_red" pos="-0.12 0 0.065"><freejoint/>
<geom name="red" type="box" size="0.02 0.02 0.02" mass="0.04" rgba="1 0 0 1"/></body>
<body name="cube_blue" pos="-0.12 0.12 0.065"><freejoint/>
<geom name="blue" type="box" size="0.02 0.02 0.02" mass="0.04" rgba="0 0 1 1"/></body>
<body name="gripper" pos="0 0 0.25">
<joint name="x" type="slide" axis="1 0 0" range="-0.25 0.25"/>
<joint name="y" type="slide" axis="0 1 0" range="-0.18 0.18"/>
<joint name="z" type="slide" axis="0 0 1" range="-0.2 0.1"/>
<geom type="box" size="0.04 0.025 0.008" mass="0.2" contype="0" conaffinity="0"/>
<body name="left" pos="-0.045 0 -0.02"><joint name="left" type="slide" axis="1 0 0" range="0 0.032"/>
<geom name="left_finger" type="box" size="0.008 0.028 0.035" mass="0.03"/></body>
<body name="right" pos="0.045 0 -0.02"><joint name="right" type="slide" axis="-1 0 0" range="0 0.032"/>
<geom name="right_finger" type="box" size="0.008 0.028 0.035" mass="0.03"/></body>
</body>
</worldbody><actuator>
<position joint="x"/><position joint="y"/><position joint="z"/>
<position joint="left"/><position joint="right"/>
</actuator></mujoco>"""


@dataclass(frozen=True)
class TabletopConfig:
    settle_duration: float = 0.2
    max_linear_velocity: float = 0.05
    max_angular_velocity: float = 0.05
    contact_tolerance: float = 0.0015
    pose_tolerance: PoseTolerance = field(default_factory=PoseTolerance)

    def __post_init__(self) -> None:
        for value in (
            self.settle_duration,
            self.max_linear_velocity,
            self.max_angular_velocity,
            self.contact_tolerance,
        ):
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("Tabletop thresholds must be finite and non-negative.")
        if self.settle_duration == 0 or not isinstance(
            self.pose_tolerance, PoseTolerance
        ):
            raise ValueError(
                "Distinct observations and valid pose tolerances are required."
            )


@dataclass(frozen=True)
class ManipulationSample:
    observed_at: ObservationTime
    poses: dict[str, dict[str, object]]
    gripper_pose: dict[str, object]
    holding: dict[str, bool]
    contacts: dict[str, tuple[int, ...]]
    inside: dict[str, bool]
    velocities: dict[str, tuple[float, ...]]

    def placement_stable(self, entity: str, config: TabletopConfig) -> bool:
        angular = math.sqrt(sum(value * value for value in self.velocities[entity][:3]))
        linear = math.sqrt(sum(value * value for value in self.velocities[entity][3:]))
        return (
            self.inside[entity]
            and not self.contacts[entity]
            and angular < config.max_angular_velocity
            and linear < config.max_linear_velocity
        )


class Tabletop:
    def __init__(self, config: TabletopConfig | None = None):
        import mujoco

        self.config = config or TabletopConfig()

        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_string(SCENE)
        self.data = mujoco.MjData(self.model)
        self.clock_id = "tabletop:" + uuid4().hex
        self.world = World(
            "tabletop",
            clock_resolver=self,
            allowed_sources=("tabletop/geometry",),
            allowed_scopes=("workspace",),
        )
        self.sequence = 0
        self.barrier: ObservationTime | None = None
        self.active_task: str | None = None
        self.task_states: dict[str, str] = {}
        self.tolerance = self.config.pose_tolerance
        self.mj.mj_forward(self.model, self.data)
        self._settle(200)

    def now(self, clock_id):
        return (
            ObservationTime(clock_id, round(self.data.time * 1e9))
            if clock_id == self.clock_id
            else None
        )

    def _settle(self, steps):
        for _ in range(steps):
            self.mj.mj_step(self.model, self.data)

    def pose(self, name):
        body = self.data.body(name)
        return {
            "xyz": [float(value) for value in body.xpos],
            "quaternion": [float(value) for value in body.xquat],
            "frame_id": "world",
        }

    def contacts(self, name):
        cube = self.model.geom("red" if name == "cube_red" else "blue").id
        fingers = {
            self.model.geom("left_finger").id,
            self.model.geom("right_finger").id,
        }
        return {
            int(contact.geom1 if contact.geom2 == cube else contact.geom2)
            for contact in self.data.contact[: self.data.ncon]
            if cube in (contact.geom1, contact.geom2)
        } & fingers

    def appearance(self, name):
        geom = self.model.geom("red" if name == "cube_red" else "blue")
        category = (
            "cube"
            if int(geom.type[0]) == int(self.mj.mjtGeom.mjGEOM_BOX)
            else self.mj.mjtGeom(int(geom.type[0])).name.removeprefix("mjGEOM_").lower()
        )
        rgba = geom.rgba
        color = (
            "red"
            if rgba[0] > 0.9 and rgba[2] < 0.1
            else "blue"
            if rgba[2] > 0.9 and rgba[0] < 0.1
            else "unknown"
        )
        return {"category": category, "color": color}

    def holding(self, name):
        cube, gripper = self.data.body(name).xpos, self.data.body("gripper").xpos
        return (
            len(self.contacts(name)) == 2
            and abs(float(cube[2] - gripper[2]) + 0.02) < 0.025
            and abs(float(cube[0] - gripper[0])) < 0.025
        )

    def inside(self, name):
        geom_name = "red" if name == "cube_red" else "blue"
        position = self.data.geom(geom_name).xpos
        rotation = self.data.geom(geom_name).xmat.reshape(3, 3)
        half_size = self.model.geom(geom_name).size
        basket = self.data.body("basket")
        local_position = basket.xmat.reshape(3, 3).T @ (position - basket.xpos)
        relative_rotation = basket.xmat.reshape(3, 3).T @ rotation
        extent = [
            sum(
                abs(float(relative_rotation[row, column])) * float(half_size[column])
                for column in range(3)
            )
            for row in range(3)
        ]
        # Interior coordinates are relative to the measured basket transform.
        # The lower margin accounts for the configured contact solver tolerance.
        return (
            abs(float(local_position[0])) + extent[0] < 0.065
            and abs(float(local_position[1])) + extent[1] < 0.065
            and 0.005 - self.config.contact_tolerance
            <= float(local_position[2]) - extent[2]
            and float(local_position[2]) + extent[2] <= 0.07
        )

    async def observe(self, *, verification=False, entity: EntityRef | None = None):
        if entity is not None and entity.id not in ("cube_red", "cube_blue", "basket"):
            reject("unknown_target")
        if verification and self.barrier is None:
            reject("verification_barrier_missing")
        # Both velocity/geometry samples are acquired after the action barrier.
        barrier = self.barrier
        if verification:
            assert barrier is not None
        samples = []
        for _ in range(2 if verification else 1):
            self._settle(
                max(1, math.ceil(self.config.settle_duration / self.model.opt.timestep))
            )
            sampled = self.now(self.clock_id)
            assert sampled is not None
            if (
                verification
                and barrier is not None
                and sampled.nanoseconds <= barrier.nanoseconds
            ):
                reject("verification_evidence_stale")
            samples.append(
                ManipulationSample(
                    sampled,
                    {
                        name: self.pose(name)
                        for name in ("cube_red", "cube_blue", "basket")
                    },
                    self.pose("gripper"),
                    {name: self.holding(name) for name in ("cube_red", "cube_blue")},
                    {
                        name: tuple(sorted(self.contacts(name)))
                        for name in ("cube_red", "cube_blue")
                    },
                    {name: self.inside(name) for name in ("cube_red", "cube_blue")},
                    {
                        name: tuple(float(value) for value in self.data.body(name).cvel)
                        for name in ("cube_red", "cube_blue")
                    },
                )
            )
        self.sequence += 1
        ref = ObservationRef("tabletop/geometry", str(self.sequence))
        latest = samples[-1]
        sampled, poses, held, inside = (
            latest.observed_at,
            latest.poses,
            latest.holding,
            latest.inside,
        )
        observation = Observation(
            ref,
            "geometry",
            sampled,
            datetime.now(timezone.utc),
            freeze_json(
                {
                    "samples": [asdict(sample) for sample in samples],
                    "geometry": {
                        name: {
                            "size": [
                                float(value) for value in self.model.geom(name).size
                            ],
                            "rgba": [
                                float(value) for value in self.model.geom(name).rgba
                            ],
                        }
                        for name in (
                            "red",
                            "blue",
                            "left_finger",
                            "right_finger",
                            "basket_floor",
                        )
                    },
                }
            ),
            "workspace",
        )
        facts: list[tuple[str, str, JsonValue | EntityRef, int | None]] = []
        for name in ("cube_red", "cube_blue", "basket"):
            facts.extend(
                [
                    (name, "pose", freeze_json(poses[name]), 1_000_000_000),
                    (
                        name,
                        "category",
                        "basket"
                        if name == "basket"
                        else self.appearance(name)["category"],
                        None,
                    ),
                ]
            )
            if name != "basket":
                color = self.appearance(name)["color"]
                facts.append((name, "color", color, None))
                if verification:
                    stable = all(
                        sample.placement_stable(name, self.config) for sample in samples
                    )
                    facts.extend(
                        [
                            (
                                name,
                                "inside",
                                EntityRef("basket") if inside[name] else None,
                                1_000_000_000,
                            ),
                            (name, "stable", stable, 1_000_000_000),
                        ]
                    )
        facts.append(
            (
                "robot",
                "holding",
                next((EntityRef(name) for name, value in held.items() if value), None),
                1_000_000_000,
            )
        )
        if entity is not None:
            facts = [fact for fact in facts if fact[0] in (entity.id, "robot")]
        claims = [
            Claim(
                ClaimRef(f"{self.sequence}:{name}:{predicate}"),
                EntityRef(name),
                predicate,
                value,
                ClaimKind.INTERPRETATION,
                ref.source,
                "workspace",
                (EvidenceRef("observation", ref.source, ref.id),),
                observation.received_at,
                sampled,
                ttl,
            )
            for name, predicate, value, ttl in facts
        ]
        return await self.world.observe(observation, claims=claims)

    async def _move(self, xyz, closed, context):
        self.data.ctrl[:] = [xyz[0], xyz[1], xyz[2] - 0.25, closed, closed]
        for _ in range(20):
            context.cancellation.raise_if_cancelled()
            self._settle(25)
            await asyncio.sleep(0)

    async def action(self, arguments, context, *, place=False):
        target = arguments["target_entity"]
        if target not in ("cube_red", "cube_blue"):
            reject("unknown_target")
        roles = {"target_pose": (target, "pose")}
        if place:
            if arguments.get("receptacle_entity") != "basket":
                reject("unknown_receptacle")
            roles.update(
                {"receptacle_pose": ("basket", "pose"), "holding": ("robot", "holding")}
            )
        current = await validate_decision(
            self.world,
            context,
            arguments,
            roles,
            self.tolerance,
            scope="workspace",
            clock_resolver=self,
        )
        if self.active_task is not None:
            reject("physical_task_active")
        from roboagent.world import WorldEntryKey
        from .contracts import pose_compatible

        self.mj.mj_forward(self.model, self.data)
        for subject, predicate in roles.values():
            if predicate != "pose":
                continue
            entry = current.lookup(
                WorldEntryKey(EntityRef(subject), predicate, "workspace")
            )
            assert entry is not None
            if not pose_compatible(entry.value, self.pose(subject), self.tolerance):
                reject("target_changed")
            xyz = self.pose(subject)["xyz"]
            if not (
                -0.24 <= xyz[0] <= 0.24
                and -0.16 <= xyz[1] <= 0.16
                and 0.04 <= xyz[2] <= 0.28
            ):
                reject("target_unreachable")
        appearance = self.appearance(target)
        for item in arguments["decision_evidence"]["selection"]["filters"]:
            if (
                item["predicate"] in appearance
                and item.get("value") != appearance[item["predicate"]]
            ):
                reject("semantic_target_changed")
        if place and not self.holding(target):
            reject("not_holding_target")
        task = uuid4().hex
        self.active_task = task
        self.task_states[task] = "running"
        try:
            if place:
                basket = self.pose("basket")["xyz"]
                approach = (basket[0], basket[1], 0.25)
                release = (basket[0], basket[1], basket[2] + 0.055)
                await self._move(approach, 0.032, context)
                await self._move(release, 0.032, context)
                await self._move(release, 0, context)
                await self._move(approach, 0, context)
            else:
                xyz = self.pose(target)["xyz"]
                await self._move((xyz[0], xyz[1], 0.25), 0, context)
                await self._move((xyz[0], xyz[1], xyz[2] + 0.02), 0, context)
                await self._move((xyz[0], xyz[1], xyz[2] + 0.02), 0.032, context)
                await self._move((xyz[0], xyz[1], 0.25), 0.032, context)
            self.task_states[task] = "completed"
            self.barrier = self.now(self.clock_id)
            return ToolJsonContent(
                freeze_json(
                    {
                        "task_id": task,
                        "status": "completed",
                        "physical_verified": False,
                        "completion_barrier": {
                            "clock_id": self.clock_id,
                            "nanoseconds": self.barrier.nanoseconds,
                        },
                    }
                )
            )
        except asyncio.CancelledError:
            # Hold the measured actuator positions and stop advancing this task.
            # Cancellation alone is not a physical standstill verification.
            self.data.ctrl[:] = self.data.qpos[-5:]
            self._settle(500)
            self.task_states[task] = "cancelled"
            raise
        finally:
            self.active_task = None

    def tools(self):
        async def observe(arguments, context):
            context.cancellation.raise_if_cancelled()
            receipt = await self.observe(
                verification=arguments.get("verification", False),
                entity=EntityRef(arguments["entity_id"])
                if "entity_id" in arguments
                else None,
            )
            return ToolJsonContent(
                freeze_json({"status": "committed", "world_revision": receipt.revision})
            )

        async def pick(arguments, context):
            return await self.action(arguments, context)

        async def place(arguments, context):
            return await self.action(arguments, context, place=True)

        def definition(name, properties, required):
            return ToolDefinition(
                name,
                name.replace("_", " "),
                freeze_json_object(
                    {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    }
                ),
            )

        action = {
            "target_entity": {"type": "string", "enum": ["cube_red", "cube_blue"]},
            "decision_evidence": decision_evidence_schema(),
        }
        return (
            Tool(
                definition("observe_scene", {"verification": {"type": "boolean"}}, []),
                observe,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
            Tool(
                definition(
                    "observe_object",
                    {
                        "entity_id": {
                            "type": "string",
                            "enum": ["cube_red", "cube_blue", "basket"],
                            "x-world-entity-ref": True,
                        },
                        "verification": {"type": "boolean"},
                    },
                    ["entity_id"],
                ),
                observe,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
            Tool(
                definition("pick_object", action, list(action)),
                pick,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
            Tool(
                definition(
                    "place_object",
                    {
                        **action,
                        "receptacle_entity": {
                            "type": "string",
                            "enum": ["basket"],
                            "x-world-entity-ref": True,
                        },
                    },
                    [*action, "receptacle_entity"],
                ),
                place,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
        )
