"""Explicit mfr3duo roserver HTTP state/action/task-control adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import cast
from datetime import datetime, timezone
import json
import math
from time import monotonic_ns
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from roboagent.message import freeze_json, freeze_json_object
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectUnknown,
    ToolErrorInfo,
    ToolJsonContent,
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
    WorldEntryKey,
)
from .contracts import (
    PhysicalPolicy,
    PoseTolerance,
    reject,
    revalidate_decision,
    validate_decision,
    decision_evidence_schema,
)


class RoserverTransport:
    def __init__(self, base_url: str, *, timeout: float = 5):
        if not base_url.startswith(("http://", "https://")) or timeout <= 0:
            raise ValueError("An explicit HTTP endpoint and deadline are required.")
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    async def request(self, method, path, body=None, *, request_id=None):
        def send():
            headers = {"Content-Type": "application/json"}
            if request_id is not None:
                headers["Idempotency-Key"] = request_id
            request = Request(
                self.base_url + path,
                data=None if body is None else json.dumps(body).encode(),
                headers=headers,
                method=method,
            )
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read(1_048_577))

        return await asyncio.to_thread(send)


class Mfr3duoBridge:
    def __init__(
        self,
        world: World,
        transport,
        robot_id: str,
        *,
        source="mfr3duo/state",
        scope="workspace",
        clock_id="host:capture",
        clock_resolver=None,
        pre_dispatch=None,
    ):
        self.policy = PhysicalPolicy(frozenset(("pick_object", "place_object")))
        self.world, self.transport, self.robot_id = world, transport, robot_id
        self.source, self.scope, self.clock_id = source, scope, clock_id
        self.path = "/api/v1/robots/" + quote(robot_id, safe="")
        self._clock_resolver = clock_resolver
        self.pre_dispatch = pre_dispatch
        self.sequence = 0
        self.pending: str | None = None
        self.uncertain = False
        self._admission = asyncio.Lock()

    def now(self, clock_id):
        if self._clock_resolver is not None and clock_id != self.clock_id:
            return self._clock_resolver.now(clock_id)
        return (
            ObservationTime(clock_id, monotonic_ns())
            if clock_id == self.clock_id
            else None
        )

    async def observe_state(self):
        state = await self.transport.request("GET", self.path + "/state")
        self.sequence += 1
        ref = ObservationRef(self.source, str(self.sequence))
        sampled = self.now(self.clock_id)
        observation = Observation(
            ref,
            "robot_state",
            sampled,
            datetime.now(timezone.utc),
            freeze_json(state),
            self.scope,
        )
        claim = Claim(
            ClaimRef(f"{ref.id}:state"),
            EntityRef("robot"),
            "backend_state",
            freeze_json(state),
            ClaimKind.INTERPRETATION,
            self.source,
            self.scope,
            (EvidenceRef("observation", self.source, ref.id),),
            observation.received_at,
            sampled,
            1_000_000_000,
        )
        # roserver state is a poll result, not proof of camera/object sampling time.
        # Retain it as backend state; do not manufacture pose/holding evidence.
        return await self.world.observe(observation, claims=(claim,))

    async def task_status(self):
        # Submission, status reconciliation and stop share one Host admission
        # boundary so an old response cannot clear a newer physical operation.
        async with self._admission:
            if self.uncertain:
                return {"status": "unknown", "task_id": self.pending}
            if self.pending is None:
                return {"status": "idle"}
            result = await self.transport.request(
                "GET", self.path + "/operations/" + quote(self.pending, safe="")
            )
            if (
                not isinstance(result, Mapping)
                or result.get("operation_id") != self.pending
                or result.get("robot_id") != self.robot_id
                or result.get("status")
                not in {"pending", "running", "succeeded", "failed", "cancelled"}
            ):
                reject("invalid_operation_status")
            error = result.get("error")
            confirmed_terminal = bool(result.get("finished_at")) and (
                result["status"] == "succeeded"
                or (
                    result["status"] == "cancelled"
                    and isinstance(error, Mapping)
                    and error.get("code") == "run_cancelled"
                )
            )
            # roserver marks unconfirmed termination as failed/recovery-required.
            # Other failures also remain blocked until an explicit confirmed stop.
            if confirmed_terminal:
                self.pending = None
                result = {
                    **result,
                    "completion_barrier": {
                        "clock_id": self.clock_id,
                        "nanoseconds": monotonic_ns(),
                    },
                }
            return result

    async def stop(self):
        async with self._admission:
            result = await self.transport.request("POST", self.path + "/stop", {})
            if result.get("stopped") is True:
                self.pending, self.uncertain = None, False
            return result

    async def action(self, arguments, context, *, kind):
        roles = {"target_pose": (arguments["target_entity"], "pose")}
        if kind == "place":
            roles["holding"] = ("robot", "holding")
            roles["receptacle_pose"] = (arguments["receptacle_entity"], "pose")
        snapshot = await validate_decision(
            self.world,
            context,
            arguments,
            roles,
            PoseTolerance(),
            scope=self.scope,
            clock_resolver=self,
            policy=self.policy,
        )
        # Do not queue another Model decision behind preflight or submission.
        if self._admission.locked() or self.pending is not None or self.uncertain:
            reject("physical_task_unresolved")
        async with self._admission:
            if self.pending is not None or self.uncertain:
                reject("physical_task_unresolved")
            if (
                self.pre_dispatch is None
                or await self.pre_dispatch(snapshot, arguments) is not True
            ):
                reject("backend_preflight_failed")
            context.cancellation.raise_if_cancelled()
            info = await self.transport.request("GET", self.path)
            if info.get("connection") != "online" or "manipulation" not in info.get(
                "capabilities", ()
            ):
                reject("backend_unavailable")
            snapshot = await revalidate_decision(
                self.world,
                context,
                arguments,
                roles,
                PoseTolerance(),
                scope=self.scope,
                clock_resolver=self,
                policy=self.policy,
            )
            context.cancellation.raise_if_cancelled()
            payload = {"kind": kind, "object_id": arguments["target_entity"]}
            if kind == "place":
                entry = snapshot.lookup(
                    WorldEntryKey(
                        EntityRef(arguments["receptacle_entity"]), "pose", self.scope
                    )
                )
                assert entry is not None  # The exact scoped key was validated above.
                pose = entry.value
                if not isinstance(pose, Mapping):
                    reject("receptacle_evidence_required")
                q = cast(Sequence[float], pose["quaternion"])
                if abs(q[1]) > 1e-6 or abs(q[2]) > 1e-6:
                    reject("unsupported_receptacle_orientation")
                xyz = cast(Sequence[float], pose["xyz"])
                payload["pose"] = {
                    "x": xyz[0],
                    "y": xyz[1],
                    "z": xyz[2],
                    "theta": math.atan2(
                        2 * (q[0] * q[3] + q[1] * q[2]),
                        1 - 2 * (q[2] * q[2] + q[3] * q[3]),
                    ),
                    "frame_id": pose["frame_id"],
                }
            request_id = uuid4().hex
            self.pending = request_id
            try:
                result = await self.transport.request(
                    "POST",
                    self.path + "/operations",
                    payload,
                    request_id=request_id,
                )
                operation_id = result.get("operation_id")
                if (
                    not isinstance(operation_id, str)
                    or not operation_id
                    or len(operation_id) > 128
                ):
                    raise ValueError("invalid_operation_receipt")
                self.pending = operation_id
            except (Exception, asyncio.CancelledError) as exc:
                self.uncertain = True
                raise ToolEffectUnknown(
                    ToolErrorInfo(
                        "physical_effect_unknown",
                        "Controller acceptance cannot be determined.",
                    )
                ) from exc
            return ToolJsonContent(
                freeze_json(
                    {
                        "status": "accepted",
                        "task_id": self.pending,
                        "world_revision": snapshot.revision,
                        "physical_verified": False,
                    }
                )
            )

    async def tools(self):
        info = await self.transport.request("GET", self.path)
        capabilities = set(info.get("capabilities", ()))

        async def observe(arguments, context):
            context.cancellation.raise_if_cancelled()
            receipt = await self.observe_state()
            return ToolJsonContent(
                freeze_json({"status": "committed", "world_revision": receipt.revision})
            )

        async def status(arguments, context):
            return ToolJsonContent(freeze_json(await self.task_status()))

        async def stop(arguments, context):
            return ToolJsonContent(freeze_json(await self.stop()))

        def definition(name, properties=None):
            props = properties or {}
            return ToolDefinition(
                name,
                name.replace("_", " "),
                freeze_json_object(
                    {
                        "type": "object",
                        "properties": props,
                        "required": list(props),
                        "additionalProperties": False,
                    }
                ),
            )

        tools = [
            Tool(
                definition("observe_robot_state"),
                observe,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
            Tool(definition("get_task_status"), status),
            Tool(
                definition("stop_robot"),
                stop,
                effect_kind=ToolEffectKind.SIDE_EFFECTING,
            ),
        ]
        if "manipulation" not in capabilities or self.pre_dispatch is None:
            return tuple(tools)
        for kind in ("pick", "place"):

            async def action(arguments, context, kind=kind):
                return await self.action(arguments, context, kind=kind)

            tools.append(
                Tool(
                    definition(
                        kind + "_object",
                        {
                            "target_entity": {
                                "type": "string",
                                "x-world-entity-ref": True,
                            },
                            "decision_evidence": decision_evidence_schema(),
                            **(
                                {
                                    "receptacle_entity": {
                                        "type": "string",
                                        "x-world-entity-ref": True,
                                    }
                                }
                                if kind == "place"
                                else {}
                            ),
                        },
                    ),
                    action,
                    effect_kind=ToolEffectKind.SIDE_EFFECTING,
                )
            )
        return tuple(tools)
