"""Immutable, domain-neutral visual evidence and derived results."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Generic, TypeVar
import math
from numbers import Real

from roboagent.message import FrozenJsonObject, canonical_json_dumps, freeze_json_object
from roboagent.model import Usage


class VisionError(ValueError):
    """Stable failure code; messages never contain image data or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def text_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise VisionError("vision_identity_invalid")


def integer(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise VisionError("vision_input_invalid")


def finite(*values: float) -> None:
    if any(
        isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v)
        for v in values
    ):
        raise VisionError("vision_input_invalid")


def utc_time(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise VisionError("vision_time_invalid")


@dataclass(frozen=True, slots=True)
class VisionLimits:
    max_encoded_image_bytes: int = 8 * 1024 * 1024
    max_decoded_pixels: int = 16_777_216
    max_depth_bytes: int = 64 * 1024 * 1024
    max_buffer_frames: int = 16
    max_buffer_bytes: int = 128 * 1024 * 1024
    max_buffer_age_ns: int = 5_000_000_000
    max_semantic_output_bytes: int = 512 * 1024
    max_objects: int = 64
    max_relations: int = 512
    max_attributes_bytes_per_object: int = 16 * 1024
    max_text_chars: int = 4096
    max_identity_chars: int = 256
    max_json_depth: int = 16
    max_roi_points: int = 65_536
    max_tracks: int = 1024
    max_missed_observations: int = 8
    max_track_age_ns: int = 5_000_000_000
    max_tracking_cache_frames: int = 128
    max_tracking_cache_bytes: int = 16 * 1024 * 1024
    semantic_timeout_seconds: float = 30

    def __post_init__(self):
        for f in fields(self):
            if f.name != "semantic_timeout_seconds":
                integer(getattr(self, f.name), minimum=1)
        finite(self.semantic_timeout_seconds)
        if self.semantic_timeout_seconds <= 0:
            raise VisionError("vision_input_invalid")


@dataclass(frozen=True, slots=True)
class CaptureTime:
    clock_id: str
    nanoseconds: int

    def __post_init__(self):
        text_id(self.clock_id)
        integer(self.nanoseconds)


@dataclass(frozen=True, slots=True)
class CaptureRef:
    stream_id: str
    clock_id: str
    capture_id: str
    sequence: int

    def __post_init__(self):
        for value in (self.stream_id, self.clock_id, self.capture_id):
            text_id(value)
        integer(self.sequence)


@dataclass(frozen=True, slots=True)
class ImageFrame:
    data: bytes
    mime_type: str
    width: int
    height: int
    limits: VisionLimits = field(
        default_factory=VisionLimits, repr=False, compare=False
    )

    def __post_init__(self):
        from .image import validate_image

        if not isinstance(self.limits, VisionLimits):
            raise VisionError("vision_input_invalid")
        validate_image(self.data, self.mime_type, self.width, self.height, self.limits)


@dataclass(frozen=True, slots=True)
class VisualCapture:
    stream_id: str
    capture_id: str
    sequence: int
    image: ImageFrame
    captured_at: CaptureTime
    received_at: datetime
    frame_id: str | None = None
    parent_ref: CaptureRef | None = None

    def __post_init__(self):
        if not isinstance(self.captured_at, CaptureTime):
            raise VisionError("vision_time_invalid")
        self.ref
        utc_time(self.received_at)
        if not isinstance(self.image, ImageFrame) or not isinstance(
            self.captured_at, CaptureTime
        ):
            raise VisionError("vision_input_invalid")
        if self.frame_id is not None:
            text_id(self.frame_id)
        if self.parent_ref is not None and not isinstance(self.parent_ref, CaptureRef):
            raise VisionError("vision_input_invalid")

    @property
    def ref(self) -> CaptureRef:
        return CaptureRef(
            self.stream_id, self.captured_at.clock_id, self.capture_id, self.sequence
        )


@dataclass(frozen=True, slots=True)
class DepthFrame:
    data: Any
    captured_at: CaptureTime
    received_at: datetime
    limits: VisionLimits = field(
        default_factory=VisionLimits, repr=False, compare=False
    )

    def __post_init__(self):
        from .image import dependencies

        if not isinstance(self.limits, VisionLimits):
            raise VisionError("vision_input_invalid")
        np, _ = dependencies()
        array = np.asarray(self.data)
        if array.ndim != 2 or not all(array.shape) or array.dtype.kind not in "fiu":
            raise VisionError("depth_shape_invalid")
        if array.size * 4 > self.limits.max_depth_bytes:
            raise VisionError("depth_limit_exceeded")
        # A bytes-backed array cannot be made writable again by a consumer.
        owned = np.array(array, dtype="<f4", order="C", copy=True)
        frozen = np.frombuffer(owned.tobytes(), dtype="<f4").reshape(owned.shape)
        object.__setattr__(self, "data", frozen)
        if not isinstance(self.captured_at, CaptureTime):
            raise VisionError("vision_time_invalid")
        utc_time(self.received_at)


@dataclass(frozen=True, slots=True)
class PinholeCamera:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    frame_id: str

    def __post_init__(self):
        integer(self.width, minimum=1)
        integer(self.height, minimum=1)
        finite(self.fx, self.fy, self.cx, self.cy)
        if self.fx <= 0 or self.fy <= 0:
            raise VisionError("camera_invalid")
        text_id(self.frame_id)
        for name in ("fx", "fy", "cx", "cy"):
            object.__setattr__(self, name, float(getattr(self, name)))

    def resized(self, width: int, height: int) -> PinholeCamera:
        integer(width, minimum=1)
        integer(height, minimum=1)
        sx, sy = width / self.width, height / self.height
        return PinholeCamera(
            width,
            height,
            self.fx * sx,
            self.fy * sy,
            (self.cx + 0.5) * sx - 0.5,
            (self.cy + 0.5) * sy - 0.5,
            self.frame_id,
        )


@dataclass(frozen=True, slots=True)
class RegisteredDepthGeometry:
    calibration_id: str
    normalized_ray_tolerance: float = 1e-6

    def __post_init__(self):
        text_id(self.calibration_id)
        finite(self.normalized_ray_tolerance)
        if self.normalized_ray_tolerance < 0:
            raise VisionError("camera_invalid")


@dataclass(frozen=True, slots=True)
class SpatialCapture:
    visual: VisualCapture
    visual_camera: PinholeCamera
    depth: DepthFrame
    depth_camera: PinholeCamera
    registration: RegisteredDepthGeometry | None = None
    calibration_metadata: FrozenJsonObject = field(default_factory=FrozenJsonObject)

    def __post_init__(self):
        if (
            not isinstance(self.visual, VisualCapture)
            or not isinstance(self.depth, DepthFrame)
            or not isinstance(self.visual_camera, PinholeCamera)
            or not isinstance(self.depth_camera, PinholeCamera)
        ):
            raise VisionError("vision_input_invalid")
        if self.registration is not None and not isinstance(
            self.registration, RegisteredDepthGeometry
        ):
            raise VisionError("spatial_registration_unavailable")
        if (self.visual.image.width, self.visual.image.height) != (
            self.visual_camera.width,
            self.visual_camera.height,
        ):
            raise VisionError("camera_dimension_mismatch")
        if self.depth.data.shape != (self.depth_camera.height, self.depth_camera.width):
            raise VisionError("camera_dimension_mismatch")
        if (
            self.visual.frame_id is not None
            and self.visual.frame_id != self.visual_camera.frame_id
        ):
            raise VisionError("camera_frame_mismatch")
        metadata = freeze_json_object(self.calibration_metadata)
        if (
            len(canonical_json_dumps(metadata).encode())
            > self.visual.image.limits.max_attributes_bytes_per_object
        ):
            raise VisionError("calibration_metadata_limit_exceeded")
        object.__setattr__(self, "calibration_metadata", metadata)

    @property
    def ref(self) -> CaptureRef:
        return self.visual.ref


@dataclass(frozen=True, slots=True)
class Point2D:
    x: float
    y: float

    def __post_init__(self):
        finite(self.x, self.y)
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        if not (0 <= self.x <= 1 and 0 <= self.y <= 1):
            raise VisionError("coordinate_invalid")

    def pixel(self, width: int, height: int) -> tuple[float, float]:
        return self.x * width - 0.5, self.y * height - 0.5

    def index(self, width: int, height: int) -> tuple[int, int]:
        return min(width - 1, math.floor(self.x * width)), min(
            height - 1, math.floor(self.y * height)
        )


@dataclass(frozen=True, slots=True)
class Box2D:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self):
        finite(self.x0, self.y0, self.x1, self.y1)
        for name in ("x0", "y0", "x1", "y1"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if not (0 <= self.x0 < self.x1 <= 1 and 0 <= self.y0 < self.y1 <= 1):
            raise VisionError("coordinate_invalid")

    def roi(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            max(0, math.floor(self.x0 * width)),
            max(0, math.floor(self.y0 * height)),
            min(width, math.ceil(self.x1 * width)),
            min(height, math.ceil(self.y1 * height)),
        )

    def contains(self, point: Point2D) -> bool:
        return self.x0 <= point.x < self.x1 and self.y0 <= point.y < self.y1


RESERVED_ATTRIBUTES = frozenset(
    {
        "xyz",
        "position",
        "pose",
        "quaternion",
        "frame_id",
        "metric_distance",
        "category",
        "region",
        "anchor",
        "local_id",
        "source",
        "scope",
        "entity_ref",
        "track_id",
        "analysis_id",
    }
)


def validate_attributes(value: object, depth: int = 0, *, maximum: int = 16) -> None:
    if depth > maximum:
        raise VisionError("semantic_output_limit_exceeded")
    if isinstance(value, dict) or isinstance(value, FrozenJsonObject):
        if RESERVED_ATTRIBUTES.intersection(value):
            raise VisionError("semantic_result_invalid")
        for child in value.values():
            validate_attributes(child, depth + 1, maximum=maximum)
    elif isinstance(value, (list, tuple)):
        for child in value:
            validate_attributes(child, depth + 1, maximum=maximum)


@dataclass(frozen=True, slots=True)
class VisualDetection:
    local_id: str
    category: str
    region: Box2D
    anchor: Point2D
    attributes: FrozenJsonObject = field(default_factory=FrozenJsonObject)
    description: str | None = None

    def __post_init__(self):
        text_id(self.local_id)
        text_id(self.category)
        if not isinstance(self.region, Box2D) or not isinstance(self.anchor, Point2D):
            raise VisionError("semantic_result_invalid")
        if not self.region.contains(self.anchor):
            raise VisionError("semantic_result_invalid")
        attributes = freeze_json_object(self.attributes)
        validate_attributes(attributes)
        object.__setattr__(self, "attributes", attributes)


@dataclass(frozen=True, slots=True)
class VisualRelation:
    subject_id: str
    predicate: str
    object_id: str

    def __post_init__(self):
        for value in (self.subject_id, self.predicate, self.object_id):
            text_id(value)


@dataclass(frozen=True, slots=True)
class VisualQuery:
    text: str | None = None
    max_objects: int = 32
    include_relations: bool = True

    def __post_init__(self):
        integer(self.max_objects, minimum=1)
        if self.text is not None and (
            not isinstance(self.text, str) or len(self.text) > 4096
        ):
            raise VisionError("semantic_result_invalid")
        if type(self.include_relations) is not bool:
            raise VisionError("semantic_result_invalid")


@dataclass(frozen=True, slots=True)
class VisualScene:
    stream_id: str
    clock_id: str
    capture_id: str
    sequence: int
    objects: tuple[VisualDetection, ...]
    relations: tuple[VisualRelation, ...] = ()
    description: str | None = None
    query: str | None = None
    object_limit_reached: bool = False

    @property
    def ref(self) -> CaptureRef:
        return CaptureRef(self.stream_id, self.clock_id, self.capture_id, self.sequence)

    def __post_init__(self):
        self.ref
        object.__setattr__(self, "objects", tuple(self.objects))
        object.__setattr__(self, "relations", tuple(self.relations))
        if any(not isinstance(d, VisualDetection) for d in self.objects) or any(
            not isinstance(r, VisualRelation) for r in self.relations
        ):
            raise VisionError("semantic_result_invalid")
        ids = {d.local_id for d in self.objects}
        triples = {(r.subject_id, r.predicate, r.object_id) for r in self.relations}
        if len(ids) != len(self.objects) or len(triples) != len(self.relations):
            raise VisionError("semantic_result_invalid")
        if any(
            r.subject_id not in ids or r.object_id not in ids for r in self.relations
        ):
            raise VisionError("semantic_result_invalid")
        if type(self.object_limit_reached) is not bool:
            raise VisionError("semantic_result_invalid")

    def validate_limits(self, limits: VisionLimits) -> None:
        if (
            len(self.objects) > limits.max_objects
            or len(self.relations) > limits.max_relations
        ):
            raise VisionError("semantic_output_limit_exceeded")
        for text in (
            self.description,
            self.query,
            *(d.description for d in self.objects),
        ):
            if text is not None and (
                not isinstance(text, str) or len(text) > limits.max_text_chars
            ):
                raise VisionError("semantic_output_limit_exceeded")
        for d in self.objects:
            if (
                len(canonical_json_dumps(d.attributes).encode())
                > limits.max_attributes_bytes_per_object
            ):
                raise VisionError("semantic_output_limit_exceeded")


@dataclass(frozen=True, slots=True)
class SemanticAnalysis:
    analysis_id: str
    scene: VisualScene
    usage: Usage | None = None

    def __post_init__(self):
        text_id(self.analysis_id)
        if (
            not isinstance(self.scene, VisualScene)
            or self.usage is not None
            and not isinstance(self.usage, Usage)
        ):
            raise VisionError("semantic_result_invalid")


class SpatialReference(str, Enum):
    VISIBLE_SURFACE = "visible_surface"
    VISIBLE_POINTS_CENTROID = "visible_points_centroid"
    OBJECT_ORIGIN = "object_origin"


class EstimateStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


def xyz(value) -> tuple[float, float, float]:
    values = tuple(value)
    if len(values) != 3:
        raise VisionError("geometry_invalid")
    finite(*values)
    return float(values[0]), float(values[1]), float(values[2])


@dataclass(frozen=True, slots=True)
class PointEstimate:
    capture_ref: CaptureRef
    detection_id: str
    xyz: tuple[float, float, float]
    frame_id: str
    reference: SpatialReference = SpatialReference.VISIBLE_SURFACE

    def __post_init__(self):
        object.__setattr__(self, "xyz", xyz(self.xyz))
        text_id(self.detection_id)
        text_id(self.frame_id)
        if not isinstance(self.reference, SpatialReference):
            raise VisionError("geometry_invalid")


@dataclass(frozen=True, slots=True)
class PositionEstimate(PointEstimate):
    reference: SpatialReference = SpatialReference.VISIBLE_POINTS_CENTROID
    point_count: int = 1
    inlier_ratio: float = 1
    spread: tuple[float, float, float] = (0.0, 0.0, 0.0)
    spread_frame_id: str | None = None

    def __post_init__(self):
        PointEstimate.__post_init__(self)
        object.__setattr__(
            self, "spread_frame_id", self.spread_frame_id or self.frame_id
        )
        assert self.spread_frame_id is not None
        text_id(self.spread_frame_id)
        integer(self.point_count, minimum=1)
        finite(self.inlier_ratio)
        object.__setattr__(self, "spread", xyz(self.spread))
        if not 0 <= self.inlier_ratio <= 1 or min(self.spread) < 0:
            raise VisionError("geometry_invalid")


@dataclass(frozen=True, slots=True)
class PoseEstimate:
    capture_ref: CaptureRef
    detection_id: str
    position: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]
    frame_id: str
    object_frame_id: str
    method: str
    reprojection_error_px: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "position", xyz(self.position))
        q = tuple(self.orientation_wxyz)
        finite(*q)
        if len(q) != 4 or abs(sum(v * v for v in q) - 1) > 1e-5:
            raise VisionError("pose_invalid")
        object.__setattr__(self, "orientation_wxyz", tuple(float(v) for v in q))
        for v in (self.detection_id, self.frame_id, self.object_frame_id, self.method):
            text_id(v)
        if self.reprojection_error_px is not None:
            finite(self.reprojection_error_px)
            if self.reprojection_error_px < 0:
                raise VisionError("pose_invalid")


@dataclass(frozen=True, slots=True)
class PrincipalAxesEstimate:
    capture_ref: CaptureRef
    detection_id: str
    frame_id: str
    directions: tuple[tuple[float, float, float], ...]
    eigenvalues: tuple[float, float, float]

    def __post_init__(self):
        rows = tuple(xyz(v) for v in self.directions)
        if len(rows) != 3 or any(abs(sum(x * x for x in r) - 1) > 1e-5 for r in rows):
            raise VisionError("geometry_invalid")
        if any(
            abs(sum(a * b for a, b in zip(rows[i], rows[j], strict=True))) > 1e-5
            for i, j in ((0, 1), (0, 2), (1, 2))
        ):
            raise VisionError("geometry_invalid")
        object.__setattr__(self, "directions", rows)
        object.__setattr__(self, "eigenvalues", xyz(self.eigenvalues))
        if min(self.eigenvalues) < 0:
            raise VisionError("geometry_invalid")
        text_id(self.detection_id)
        text_id(self.frame_id)


T = TypeVar("T", bound=PointEstimate | PoseEstimate | PrincipalAxesEstimate)


@dataclass(frozen=True, slots=True)
class EstimateResult(Generic[T]):
    capture_ref: CaptureRef
    detection_id: str
    status: EstimateStatus
    estimate: T | None
    reason: str | None = None

    def __post_init__(self):
        self._validate()

    def _validate(self):
        text_id(self.detection_id)
        if not isinstance(self.status, EstimateStatus):
            raise VisionError("estimate_invalid")
        if self.status is EstimateStatus.AVAILABLE:
            if self.estimate is None or self.reason is not None:
                raise VisionError("estimate_invalid")
            if (
                self.estimate.capture_ref != self.capture_ref
                or self.estimate.detection_id != self.detection_id
            ):
                raise VisionError("estimate_binding_invalid")
        elif self.estimate is not None or not self.reason:
            raise VisionError("estimate_invalid")


@dataclass(frozen=True, slots=True)
class PoseResult(EstimateResult[PoseEstimate]):
    def __post_init__(self):
        self._validate()
        if self.estimate is not None and not isinstance(self.estimate, PoseEstimate):
            raise VisionError("pose_invalid")


@dataclass(frozen=True, slots=True)
class PositionResult(EstimateResult[PositionEstimate]):
    def __post_init__(self):
        self._validate()
        if self.estimate is not None and not isinstance(
            self.estimate, PositionEstimate
        ):
            raise VisionError("estimate_invalid")


@dataclass(frozen=True, slots=True)
class OrientationResult(EstimateResult[PrincipalAxesEstimate]):
    def __post_init__(self):
        self._validate()
        if self.estimate is not None and not isinstance(
            self.estimate, PrincipalAxesEstimate
        ):
            raise VisionError("estimate_invalid")


@dataclass(frozen=True, slots=True)
class PointResult(EstimateResult[PointEstimate]):
    def __post_init__(self):
        self._validate()
        if self.estimate is not None and (
            type(self.estimate) is not PointEstimate
            or self.estimate.reference is not SpatialReference.VISIBLE_SURFACE
        ):
            raise VisionError("estimate_invalid")


@dataclass(frozen=True, slots=True)
class SpatialResults:
    capture_ref: CaptureRef
    surface: tuple[PointResult, ...] = ()
    positions: tuple[PositionResult, ...] = ()
    poses: tuple[PoseResult, ...] = ()
    orientations: tuple[OrientationResult, ...] = ()

    def __post_init__(self):
        expected = {
            "surface": PointResult,
            "positions": PositionResult,
            "poses": PoseResult,
            "orientations": OrientationResult,
        }
        for name in ("surface", "positions", "poses", "orientations"):
            items = tuple(getattr(self, name))
            object.__setattr__(self, name, items)
            if any(not isinstance(r, expected[name]) for r in items):
                raise VisionError("estimate_invalid")
            if len({r.detection_id for r in items}) != len(items) or any(
                r.capture_ref != self.capture_ref for r in items
            ):
                raise VisionError("estimate_binding_invalid")
