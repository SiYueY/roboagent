"""Demand-driven RGB-D geometry at the Host boundary; World never accesses sensors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from roboagent.message import freeze_json, freeze_json_object
from roboagent.world import (
    Claim,
    ClaimKind,
    ClaimRef,
    EntityRef,
    EvidenceRef,
    Observation,
    ObservationTime,
    World,
)


@dataclass(frozen=True)
class RGBDSample:
    """Host-calibrated aligned RGB/depth pixel grid, with original capture times."""

    observation: Observation
    depth_time: ObservationTime
    width: int
    height: int
    depth_metres: tuple[float, ...]
    intrinsics: tuple[float, float, float, float]
    frame_id: str

    def __post_init__(self):
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width < 1
            or self.height < 1
            or len(self.depth_metres) != self.width * self.height
        ):
            raise ValueError("Invalid depth dimensions.")
        if (
            len(self.intrinsics) != 4
            or not all(
                type(v) in (int, float) and math.isfinite(v) for v in self.intrinsics
            )
            or min(self.intrinsics[:2]) <= 0
        ):
            raise ValueError("Invalid camera intrinsics.")
        object.__setattr__(self, "depth_metres", tuple(self.depth_metres))
        object.__setattr__(self, "intrinsics", tuple(self.intrinsics))


@dataclass(frozen=True)
class Detection:
    entity: EntityRef
    category: str
    attributes: Mapping[str, object]
    bbox: tuple[int, int, int, int]

    def __post_init__(self):
        if (
            not isinstance(self.entity, EntityRef)
            or not isinstance(self.category, str)
            or not self.category.strip()
        ):
            raise ValueError("Invalid detection identity/category.")
        if len(self.bbox) != 4 or any(type(v) is not int for v in self.bbox):
            raise ValueError("Bounding boxes require four integer pixel coordinates.")
        object.__setattr__(self, "bbox", tuple(self.bbox))
        object.__setattr__(self, "attributes", freeze_json_object(self.attributes))


class RGBDAdapter:
    def __init__(
        self,
        world: World,
        *,
        source: str,
        scope: str,
        max_skew_ns: int,
        transform: Callable[
            [str, ObservationTime, tuple[float, float, float]],
            Awaitable[tuple[float, float, float] | None],
        ],
        pose_ttl_ns: int = 1_000_000_000,
        pose_from_sample: Callable[
            [RGBDSample, Detection], Awaitable[Mapping[str, object] | None]
        ]
        | None = None,
    ):
        if (
            type(max_skew_ns) is not int
            or max_skew_ns < 0
            or type(pose_ttl_ns) is not int
            or pose_ttl_ns < 0
        ):
            raise ValueError("Timing limits must be non-negative nanoseconds.")
        self.world, self.source, self.scope = world, source, scope
        self.pose_from_sample = pose_from_sample
        self.max_skew_ns, self.pose_ttl_ns, self.transform = (
            max_skew_ns,
            pose_ttl_ns,
            transform,
        )

    async def commit(self, sample: RGBDSample, detections: tuple[Detection, ...]):
        observed = sample.observation.observed_at
        if (
            observed.clock_id != sample.depth_time.clock_id
            or abs(observed.nanoseconds - sample.depth_time.nanoseconds)
            > self.max_skew_ns
        ):
            raise ValueError("rgb_depth_skew")
        if (
            sample.observation.scope != self.scope
            or sample.observation.ref.source != self.source
        ):
            raise ValueError("observation_binding_mismatch")
        claims = []
        seen = set()
        for detection in detections:
            if detection.entity in seen:
                raise ValueError("duplicate_detection_entity")
            seen.add(detection.entity)
            x0, y0, x1, y1 = detection.bbox
            if not 0 <= x0 < x1 <= sample.width or not 0 <= y0 < y1 <= sample.height:
                raise ValueError("invalid_bbox")
            depths = sorted(
                sample.depth_metres[y * sample.width + x]
                for y in range(y0, y1)
                for x in range(x0, x1)
                if math.isfinite(sample.depth_metres[y * sample.width + x])
                and sample.depth_metres[y * sample.width + x] > 0
            )
            if not depths:
                raise ValueError("depth_unavailable")
            z = depths[len(depths) // 2]
            fx, fy, cx, cy = sample.intrinsics
            point = (
                ((x0 + x1 - 1) / 2 - cx) * z / fx,
                ((y0 + y1 - 1) / 2 - cy) * z / fy,
                z,
            )
            transformed = await self.transform(sample.frame_id, observed, point)
            if (
                transformed is None
                or len(transformed) != 3
                or not all(math.isfinite(v) for v in transformed)
            ):
                raise ValueError("historical_tf_unavailable")
            values = {
                "category": detection.category,
                **detection.attributes,
                "position": {"xyz": list(transformed), "frame_id": "world"},
            }
            # A visible surface point is not an object-frame origin. Full pose
            # must come from trusted Host geometry on this exact RGB-D capture.
            if self.pose_from_sample is not None:
                pose = await self.pose_from_sample(sample, detection)
                if pose is not None:
                    from .contracts import PoseTolerance, pose_compatible

                    if (
                        not isinstance(pose, Mapping)
                        or pose.get("frame_id") != "world"
                        or not pose_compatible(pose, pose, PoseTolerance())
                    ):
                        raise ValueError("invalid_measured_pose")
                    values["pose"] = dict(pose)
            # A centroid alone must not become a manipulation-ready full pose.
            if any(
                name in detection.attributes
                for name in (
                    "pose",
                    "position",
                    "quaternion",
                    "orientation",
                    "frame_id",
                    "category",
                    "holding",
                    "inside",
                    "on",
                )
            ):
                raise ValueError("untrusted_geometry_attribute")
            for predicate, value in values.items():
                claims.append(
                    Claim(
                        ClaimRef(
                            f"{sample.observation.ref.id}:{detection.entity.id}:{predicate}"
                        ),
                        detection.entity,
                        predicate,
                        freeze_json(value),
                        ClaimKind.INTERPRETATION,
                        self.source,
                        self.scope,
                        (
                            EvidenceRef(
                                "observation", self.source, sample.observation.ref.id
                            ),
                        ),
                        datetime.now(timezone.utc),
                        observed,
                        self.pose_ttl_ns if predicate in ("position", "pose") else None,
                    )
                )
        return await self.world.observe(sample.observation, claims=claims)


class CloudSemantics:
    """Use the existing Model interface; association remains an explicit Host callback."""

    def __init__(
        self,
        model,
        associate: Callable[[Observation, Mapping[str, object]], EntityRef | None],
        *,
        timeout: float = 30,
    ):
        if timeout <= 0:
            raise ValueError("Perception deadline must be positive.")
        self.model, self.associate, self.timeout = model, associate, timeout

    async def detect(self, observation: Observation, image):
        import asyncio
        import json
        from jsonschema import Draft202012Validator
        from roboagent.context import MessageSegment, ModelContext
        from roboagent.message import TextContent, UserMessage
        from roboagent.model import collect_model_stream

        schema = {
            "type": "object",
            "required": ["observation_id", "source", "detections"],
            "additionalProperties": False,
            "properties": {
                "observation_id": {"const": observation.ref.id},
                "source": {"const": observation.ref.source},
                "detections": {
                    "type": "array",
                    "maxItems": 64,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["local_id", "category", "attributes", "bbox"],
                        "properties": {
                            "local_id": {"type": "string", "minLength": 1},
                            "category": {"type": "string", "minLength": 1},
                            "attributes": {"type": "object"},
                            "bbox": {
                                "type": "array",
                                "minItems": 4,
                                "maxItems": 4,
                                "items": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                },
            },
        }
        prompt = (
            "Return only JSON matching this schema. Bounding boxes are pixel [x0,y0,x1,y1], exclusive upper bounds. Never return robot coordinates. Schema: "
            + json.dumps(schema)
        )
        context = ModelContext(
            prompt,
            (
                MessageSegment(
                    UserMessage(
                        (TextContent("Describe this captured observation."), image)
                    )
                ),
            ),
            (),
        )
        async with asyncio.timeout(self.timeout):
            response = await collect_model_stream(self.model, context)
        if response.message.tool_calls:
            raise ValueError("unexpected_perception_tool_calls")
        result = json.loads(
            "".join(
                part.text
                for part in response.message.content
                if isinstance(part, TextContent)
            )
        )
        Draft202012Validator(schema).validate(result)
        ids = [item["local_id"] for item in result["detections"]]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_detection_id")
        detections = []
        for item in result["detections"]:
            entity = self.associate(observation, freeze_json_object(item))
            if not isinstance(entity, EntityRef):
                raise ValueError("entity_association_unresolved")
            detections.append(
                Detection(
                    entity,
                    item["category"],
                    freeze_json_object(item["attributes"]),
                    tuple(item["bbox"]),
                )
            )
        return tuple(detections)


def observation_tools(
    capture, semantics: CloudSemantics, geometry: RGBDAdapter, *, timeout: float = 30
):
    """Compose capture, VLM and geometry into explicit synchronous Observation Tools.

    capture(target: EntityRef | None) returns (RGBDSample, ImageContent). The Host
    owns buffers/media and must supply original device times and calibration.
    """
    import asyncio
    from roboagent.message import freeze_json_object
    from roboagent.tool import Tool, ToolDefinition, ToolEffectKind, ToolJsonContent

    if timeout <= 0:
        raise ValueError("Observation deadline must be positive.")

    async def observe(arguments, context):
        context.cancellation.raise_if_cancelled()
        target = EntityRef(arguments["entity_id"]) if "entity_id" in arguments else None
        async with asyncio.timeout(timeout):
            sample, image = await capture(target)
            detections = await semantics.detect(sample.observation, image)
            context.cancellation.raise_if_cancelled()
            if target is not None and not any(d.entity == target for d in detections):
                raise ValueError("target_not_observed")
            receipt = await geometry.commit(sample, detections)
        return ToolJsonContent(
            freeze_json(
                {
                    "status": "committed",
                    "world_id": receipt.world_id,
                    "world_revision": receipt.revision,
                    "observation_ref": {
                        "source": sample.observation.ref.source,
                        "id": sample.observation.ref.id,
                    },
                }
            )
        )

    tools = []
    for name in ("observe_scene", "observe_object"):
        properties = (
            {}
            if name == "observe_scene"
            else {
                "entity_id": {
                    "type": "string",
                    "minLength": 1,
                    "x-world-entity-ref": True,
                }
            }
        )
        definition = ToolDefinition(
            name,
            "Capture new RGB-D and semantic evidence, then atomically commit World cognition.",
            freeze_json_object(
                {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            ),
        )
        tools.append(
            Tool(definition, observe, effect_kind=ToolEffectKind.SIDE_EFFECTING)
        )
    return tuple(tools)
