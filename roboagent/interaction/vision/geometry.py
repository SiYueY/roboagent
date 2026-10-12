"""CPU spatial estimates: visible evidence is never silently promoted to object pose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import math

from .image import dependencies
from .types import (
    CaptureRef,
    CaptureTime,
    EstimateStatus,
    OrientationResult,
    PinholeCamera,
    Point2D,
    PointEstimate,
    PointResult,
    PoseEstimate,
    PoseResult,
    PositionEstimate,
    PositionResult,
    PrincipalAxesEstimate,
    SpatialCapture,
    SpatialResults,
    VisionError,
    VisionLimits,
    VisualScene,
    finite,
    integer,
    text_id,
    xyz,
)


@dataclass(frozen=True, slots=True)
class PointSample:
    xyz: tuple[float, float, float]
    frame_id: str

    def __post_init__(self):
        object.__setattr__(self, "xyz", xyz(self.xyz))
        text_id(self.frame_id)


@dataclass(frozen=True, slots=True)
class PointSet:
    points: tuple[tuple[float, float, float], ...]
    frame_id: str

    def __post_init__(self):
        object.__setattr__(self, "points", tuple(xyz(p) for p in self.points))
        text_id(self.frame_id)


class SpatialProjector(Protocol):
    def point_at(
        self, capture: SpatialCapture, point: Point2D
    ) -> PointSample | None: ...
    def points_in(
        self, capture: SpatialCapture, region, *, max_points: int
    ) -> PointSet: ...


class TransformProvider(Protocol):
    async def transform_point(
        self, point, *, source_frame: str, target_frame: str, captured_at: CaptureTime
    ) -> tuple[float, float, float] | None: ...
    async def transform_pose(
        self, pose: PoseEstimate, *, target_frame: str, captured_at: CaptureTime
    ) -> PoseEstimate | None: ...


@dataclass(frozen=True, slots=True)
class SpatialTimingPolicy:
    max_rgb_depth_skew_ns: int = 5_000_000

    def __post_init__(self):
        integer(self.max_rgb_depth_skew_ns)


@dataclass(frozen=True, slots=True)
class GeometryPolicy:
    absolute_depth_threshold: float = 0.02
    relative_depth_threshold: float = 0.02
    neighborhood_radius: int = 1
    min_points: int = 8
    mad_multiplier: float = 3.5
    axis_separation: float = 0.05

    def __post_init__(self):
        finite(
            self.absolute_depth_threshold,
            self.relative_depth_threshold,
            self.mad_multiplier,
            self.axis_separation,
        )
        if (
            min(
                self.absolute_depth_threshold,
                self.relative_depth_threshold,
                self.mad_multiplier,
                self.axis_separation,
            )
            < 0
            or self.axis_separation > 1
        ):
            raise VisionError("geometry_policy_invalid")
        integer(self.neighborhood_radius)
        integer(self.min_points, minimum=3)


def timing(capture: SpatialCapture, policy: SpatialTimingPolicy) -> None:
    a, b = capture.visual.captured_at, capture.depth.captured_at
    if a.clock_id != b.clock_id:
        raise VisionError("rgb_depth_clock_mismatch")
    if abs(a.nanoseconds - b.nanoseconds) > policy.max_rgb_depth_skew_ns:
        raise VisionError("rgb_depth_skew")


def deproject(camera: PinholeCamera, u, v, z):
    return ((u - camera.cx) * z / camera.fx, (v - camera.cy) * z / camera.fy, z)


class RegisteredDepthProjector:
    def validate(self, capture: SpatialCapture):
        a, b = capture.visual_camera, capture.depth_camera
        registration = capture.registration
        if registration is None or a.frame_id != b.frame_id:
            raise VisionError("spatial_registration_unavailable")
        va = (
            a.fx / a.width,
            a.fy / a.height,
            (a.cx + 0.5) / a.width,
            (a.cy + 0.5) / a.height,
        )
        vb = (
            b.fx / b.width,
            b.fy / b.height,
            (b.cx + 0.5) / b.width,
            (b.cy + 0.5) / b.height,
        )
        if any(
            abs(x - y) > registration.normalized_ray_tolerance
            for x, y in zip(va, vb, strict=True)
        ):
            raise VisionError("spatial_registration_unavailable")

    def point_at(self, capture, point):
        self.validate(capture)
        camera = capture.depth_camera
        u, v = point.index(camera.width, camera.height)
        z = float(capture.depth.data[v, u])
        return (
            PointSample(deproject(camera, u, v, z), camera.frame_id)
            if math.isfinite(z) and z > 0
            else None
        )

    def points_in(self, capture, region, *, max_points):
        self.validate(capture)
        np, _ = dependencies()
        camera = capture.depth_camera
        x0, y0, x1, y1 = region.roi(camera.width, camera.height)
        v, u = np.nonzero(
            np.isfinite(capture.depth.data[y0:y1, x0:x1])
            & (capture.depth.data[y0:y1, x0:x1] > 0)
        )
        if len(u) > max_points:
            index = np.linspace(0, len(u) - 1, max_points, dtype=int)
            u, v = u[index], v[index]
        points = tuple(
            deproject(
                camera,
                int(x + x0),
                int(y + y0),
                float(capture.depth.data[y + y0, x + x0]),
            )
            for x, y in zip(u, v, strict=True)
        )
        return PointSet(points, camera.frame_id)


class SpatialAnalyzer:
    def __init__(
        self,
        *,
        projector: SpatialProjector | None = None,
        policy: GeometryPolicy | None = None,
        timing_policy: SpatialTimingPolicy | None = None,
        limits: VisionLimits | None = None,
    ):
        self.projector = projector or RegisteredDepthProjector()
        self.policy = policy or GeometryPolicy()
        self.timing_policy = timing_policy or SpatialTimingPolicy()
        self.limits = limits or VisionLimits()

    def _surface(self, capture, detection):
        if not isinstance(self.projector, RegisteredDepthProjector):
            return self.projector.point_at(capture, detection.anchor)
        self.projector.validate(capture)
        np, _ = dependencies()
        camera = capture.depth_camera
        u, v = detection.anchor.index(camera.width, camera.height)
        r = self.policy.neighborhood_radius
        window = capture.depth.data[
            max(0, v - r) : min(camera.height, v + r + 1),
            max(0, u - r) : min(camera.width, u + r + 1),
        ]
        valid = window[np.isfinite(window) & (window > 0)]
        if not len(valid):
            return None
        return PointSample(
            deproject(camera, u, v, float(np.median(valid))), camera.frame_id
        )

    def _foreground(self, capture, detection, surface):
        np, cv = dependencies()
        if not isinstance(self.projector, RegisteredDepthProjector):
            points = self.projector.points_in(
                capture, detection.region, max_points=self.limits.max_roi_points
            )
            if (
                len(points.points) > self.limits.max_roi_points
                or points.frame_id != surface.frame_id
            ):
                raise VisionError("projector_result_invalid")
            return np.asarray(points.points, dtype=float).reshape(-1, 3), len(
                points.points
            )
        camera = capture.depth_camera
        x0, y0, x1, y1 = detection.region.roi(camera.width, camera.height)
        depth = capture.depth.data[y0:y1, x0:x1]
        z = surface.xyz[2]
        mask = (
            np.isfinite(depth)
            & (depth > 0)
            & (
                abs(depth - z)
                <= self.policy.absolute_depth_threshold
                + self.policy.relative_depth_threshold * z
            )
        ).astype("uint8")
        # Closing fills small depth holes; retain only the anchor component.
        mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        u, v = detection.anchor.index(camera.width, camera.height)
        _, labels = cv.connectedComponents(mask, connectivity=8)
        label = labels[v - y0, u - x0]
        if label == 0:
            raise VisionError("depth_foreground_unavailable")
        ys, xs = np.nonzero(
            (labels == label)
            & np.isfinite(depth)
            & (depth > 0)
            & (
                abs(depth - z)
                <= self.policy.absolute_depth_threshold
                + self.policy.relative_depth_threshold * z
            )
        )
        original = len(xs)
        if original > self.limits.max_roi_points:
            index = np.linspace(0, original - 1, self.limits.max_roi_points, dtype=int)
            xs, ys = xs[index], ys[index]
        points = np.array(
            [
                deproject(camera, int(x + x0), int(y + y0), float(depth[y, x]))
                for x, y in zip(xs, ys, strict=True)
            ],
            dtype=float,
        ).reshape(-1, 3)
        return points, original

    def analyze(self, capture: SpatialCapture, scene: VisualScene) -> SpatialResults:
        if capture.ref != scene.ref:
            raise VisionError("estimate_binding_invalid")
        scene.validate_limits(self.limits)
        surface_results = []
        positions = []
        orientations = []
        np, _ = dependencies()
        for detection in scene.objects:
            try:
                timing(capture, self.timing_policy)
                sample = self._surface(capture, detection)
                if sample is None:
                    raise VisionError("depth_unavailable")
                surface_results.append(
                    PointResult(
                        capture.ref,
                        detection.local_id,
                        EstimateStatus.AVAILABLE,
                        PointEstimate(
                            capture.ref, detection.local_id, sample.xyz, sample.frame_id
                        ),
                    )
                )
                points, original = self._foreground(capture, detection, sample)
                if len(points) < self.policy.min_points:
                    raise VisionError("depth_insufficient_points")
                center = np.median(points, axis=0)
                distance = np.linalg.norm(points - center, axis=1)
                median = float(np.median(distance))
                mad = float(np.median(abs(distance - median)))
                good = distance <= median + self.policy.mad_multiplier * max(mad, 1e-9)
                filtered = points[good]
                if len(filtered) < self.policy.min_points:
                    raise VisionError("depth_insufficient_points")
                centroid = np.mean(filtered, axis=0)
                spread = np.std(filtered, axis=0)
                positions.append(
                    PositionResult(
                        capture.ref,
                        detection.local_id,
                        EstimateStatus.AVAILABLE,
                        PositionEstimate(
                            capture.ref,
                            detection.local_id,
                            xyz(float(x) for x in centroid),
                            sample.frame_id,
                            point_count=len(filtered),
                            inlier_ratio=len(filtered) / len(points),
                            spread=xyz(float(x) for x in spread),
                        ),
                    )
                )
                eigenvalues, axes = np.linalg.eigh(np.cov(filtered.T))
                order = np.argsort(eigenvalues)[::-1]
                eigenvalues = np.maximum(0, eigenvalues[order])
                axes = axes[:, order].T
                if eigenvalues[0] <= 1e-12 or any(
                    (eigenvalues[i] - eigenvalues[i + 1]) / max(eigenvalues[i], 1e-12)
                    < self.policy.axis_separation
                    for i in (0, 1)
                ):
                    orientations.append(
                        OrientationResult(
                            capture.ref,
                            detection.local_id,
                            EstimateStatus.AMBIGUOUS,
                            None,
                            "principal_axes_degenerate",
                        )
                    )
                else:
                    for row in axes:
                        if row[np.argmax(abs(row))] < 0:
                            row *= -1
                    orientations.append(
                        OrientationResult(
                            capture.ref,
                            detection.local_id,
                            EstimateStatus.AVAILABLE,
                            PrincipalAxesEstimate(
                                capture.ref,
                                detection.local_id,
                                sample.frame_id,
                                tuple(xyz(float(x) for x in row) for row in axes),
                                xyz(float(x) for x in eigenvalues),
                            ),
                        )
                    )
            except VisionError as exc:
                if not any(
                    r.detection_id == detection.local_id for r in surface_results
                ):
                    surface_results.append(
                        PointResult(
                            capture.ref,
                            detection.local_id,
                            EstimateStatus.UNAVAILABLE,
                            None,
                            exc.code,
                        )
                    )
                if not any(r.detection_id == detection.local_id for r in positions):
                    positions.append(
                        PositionResult(
                            capture.ref,
                            detection.local_id,
                            EstimateStatus.UNAVAILABLE,
                            None,
                            exc.code,
                        )
                    )
                orientations.append(
                    OrientationResult(
                        capture.ref,
                        detection.local_id,
                        EstimateStatus.UNAVAILABLE,
                        None,
                        exc.code,
                    )
                )
        return SpatialResults(
            capture.ref,
            tuple(surface_results),
            tuple(positions),
            orientations=tuple(orientations),
        )


@dataclass(frozen=True, slots=True)
class PnPCorrespondences:
    capture_ref: CaptureRef
    detection_id: str
    object_frame_id: str
    object_points: tuple[tuple[float, float, float], ...]
    image_points: tuple[Point2D, ...]

    def __post_init__(self):
        text_id(self.detection_id)
        text_id(self.object_frame_id)
        object.__setattr__(
            self, "object_points", tuple(xyz(p) for p in self.object_points)
        )
        object.__setattr__(self, "image_points", tuple(self.image_points))
        if len(self.object_points) != len(self.image_points):
            raise VisionError("pnp_correspondences_invalid")


@dataclass(frozen=True, slots=True)
class PnPPolicy:
    method: str = "auto"
    max_reprojection_rmse_px: float = 2.0
    min_positive_depth_ratio: float = 1.0
    ambiguity_margin_px: float = 0.1

    def __post_init__(self):
        finite(
            self.max_reprojection_rmse_px,
            self.min_positive_depth_ratio,
            self.ambiguity_margin_px,
        )
        if (
            self.method not in {"auto", "ippe", "p3p", "sqpnp", "iterative"}
            or not 0 < self.min_positive_depth_ratio <= 1
            or min(self.max_reprojection_rmse_px, self.ambiguity_margin_px) < 0
        ):
            raise VisionError("pnp_policy_invalid")


def rotation_quaternion(rotation):
    np, _ = dependencies()
    # Symmetric eigenproblem avoids unstable trace divisions near 180 degrees.
    r = rotation
    k = (
        np.array(
            [
                [
                    r[0, 0] - r[1, 1] - r[2, 2],
                    r[0, 1] + r[1, 0],
                    r[0, 2] + r[2, 0],
                    r[2, 1] - r[1, 2],
                ],
                [
                    r[0, 1] + r[1, 0],
                    r[1, 1] - r[0, 0] - r[2, 2],
                    r[1, 2] + r[2, 1],
                    r[0, 2] - r[2, 0],
                ],
                [
                    r[0, 2] + r[2, 0],
                    r[1, 2] + r[2, 1],
                    r[2, 2] - r[0, 0] - r[1, 1],
                    r[1, 0] - r[0, 1],
                ],
                [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1], r.trace()],
            ]
        )
        / 3
    )
    _, vectors = np.linalg.eigh(k)
    q = vectors[:, -1][[3, 0, 1, 2]]
    if q[next((i for i, x in enumerate(q) if abs(x) > 1e-12), 0)] < 0:
        q = -q
    return tuple(float(x) for x in q / np.linalg.norm(q))


def solve_pnp(
    correspondences: PnPCorrespondences,
    camera: PinholeCamera,
    *,
    policy: PnPPolicy | None = None,
) -> PoseResult:
    policy = policy or PnPPolicy()
    ref, det = correspondences.capture_ref, correspondences.detection_id
    np, cv = dependencies()

    def unavailable(reason):
        return PoseResult(ref, det, EstimateStatus.UNAVAILABLE, None, reason)

    points = np.asarray(correspondences.object_points, dtype=float)
    if len(points) < 4 or np.linalg.matrix_rank(points - points.mean(axis=0)) < 2:
        return unavailable("pnp_correspondences_invalid")
    image = np.array(
        [p.pixel(camera.width, camera.height) for p in correspondences.image_points],
        dtype=float,
    )
    matrix = np.array(
        [[camera.fx, 0, camera.cx], [0, camera.fy, camera.cy], [0, 0, 1]], dtype=float
    )
    planar = np.linalg.matrix_rank(points - points.mean(axis=0)) == 2
    method = (
        ("ippe" if planar else "sqpnp") if policy.method == "auto" else policy.method
    )
    flags = {
        "ippe": cv.SOLVEPNP_IPPE,
        "p3p": cv.SOLVEPNP_P3P,
        "sqpnp": cv.SOLVEPNP_SQPNP,
        "iterative": cv.SOLVEPNP_ITERATIVE,
    }
    if (
        method == "ippe"
        and not planar
        or method == "p3p"
        and len(points) != 4
        or method == "iterative"
        and not planar
        and len(points) < 6
    ):
        return unavailable("pnp_correspondences_invalid")
    try:
        solved, rvecs, tvecs, *_ = cv.solvePnPGeneric(
            points, image, matrix, None, flags=flags[method]
        )
        candidates = []
        for rvec, tvec in zip(rvecs, tvecs, strict=True):
            rotation = cv.Rodrigues(rvec)[0]
            transformed = points @ rotation.T + tvec.reshape(1, 3)
            if (
                not np.isfinite(transformed).all()
                or np.mean(transformed[:, 2] > 0) < policy.min_positive_depth_ratio
            ):
                continue
            projected = cv.projectPoints(points, rvec, tvec, matrix, None)[0].reshape(
                -1, 2
            )
            error = float(np.sqrt(np.mean(np.sum((projected - image) ** 2, axis=1))))
            if error <= policy.max_reprojection_rmse_px:
                candidates.append((error, rotation, tvec.reshape(3)))
        candidates.sort(key=lambda c: c[0])
        if not solved or not candidates:
            return unavailable("pnp_solution_unavailable")
        if (
            len(candidates) > 1
            and candidates[1][0] - candidates[0][0] <= policy.ambiguity_margin_px
        ):
            return PoseResult(
                ref, det, EstimateStatus.AMBIGUOUS, None, "pose_multiple_solutions"
            )
        error, rotation, translation = candidates[0]
        estimate = PoseEstimate(
            ref,
            det,
            xyz(float(x) for x in translation),
            rotation_quaternion(rotation),
            camera.frame_id,
            correspondences.object_frame_id,
            "pnp:" + method,
            error,
        )
        return PoseResult(ref, det, EstimateStatus.AVAILABLE, estimate)
    except cv.error:
        return unavailable("pnp_solution_unavailable")


async def transform_pose(
    result: PoseResult,
    provider: TransformProvider,
    *,
    target_frame: str,
    captured_at: CaptureTime,
) -> PoseResult:
    if captured_at.clock_id != result.capture_ref.clock_id:
        raise VisionError("transform_time_invalid")
    if result.estimate is None:
        return result
    if result.estimate.frame_id == target_frame:
        return result
    transformed = await provider.transform_pose(
        result.estimate, target_frame=target_frame, captured_at=captured_at
    )
    if transformed is None:
        return PoseResult(
            result.capture_ref,
            result.detection_id,
            EstimateStatus.UNAVAILABLE,
            None,
            "transform_unavailable",
        )
    if not isinstance(transformed, PoseEstimate):
        raise VisionError("transform_result_invalid")
    if (
        transformed.frame_id != target_frame
        or transformed.capture_ref != result.capture_ref
        or transformed.detection_id != result.detection_id
        or transformed.object_frame_id != result.estimate.object_frame_id
    ):
        raise VisionError("transform_result_invalid")
    return PoseResult(
        result.capture_ref, result.detection_id, EstimateStatus.AVAILABLE, transformed
    )
