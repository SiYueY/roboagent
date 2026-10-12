import asyncio
from dataclasses import replace

import numpy as np
import cv2
import pytest

from roboagent.interaction.vision import (
    Box2D,
    CaptureRef,
    CaptureTime,
    DepthFrame,
    EstimateStatus,
    ImageFrame,
    PinholeCamera,
    PnPCorrespondences,
    PnPPolicy,
    Point2D,
    PointSample,
    PointSet,
    PoseEstimate,
    PoseResult,
    SpatialAnalyzer,
    SpatialReference,
    VisionError,
    VisionLimits,
    solve_pnp,
    transform_pose,
)
from tests.vision.support import capture, scene, RECEIVED


@pytest.mark.parametrize(
    "size",
    [
        (320, 240),
        (640, 480),
        (853, 481),
        (1024, 768),
        (1280, 720),
        (1920, 1080),
        (720, 1280),
    ],
)
def test_resolution_coordinates_and_valid_decode(size):
    sample = capture(*size)
    w, h = size
    p = Point2D((12 + 0.5) / w, (9 + 0.5) / h)
    assert p.index(w, h) == (12, 9)
    assert p.pixel(w, h) == pytest.approx((12, 9))
    assert Box2D(0, 0, 1, 1).roi(w, h) == (0, 0, w, h)
    with pytest.raises(VisionError, match="image_dimension_mismatch"):
        ImageFrame(sample.image.data, "image/png", w + 1, h)


def test_depth_ownership_and_estimate_gate():
    original = np.ones((20, 30), dtype=float)
    frame = DepthFrame(original, CaptureTime("c", 0), RECEIVED)
    original[:] = 4
    assert np.all(frame.data == 1)
    with pytest.raises(ValueError):
        frame.data.setflags(write=True)
    ref = CaptureRef("s", "c", "1", 1)
    with pytest.raises(VisionError):
        PoseResult(
            ref,
            "a",
            EstimateStatus.AMBIGUOUS,
            PoseEstimate(ref, "a", (0, 0, 1), (1, 0, 0, 0), "c", "object", "host"),
            "ambiguous",
        )


@pytest.mark.parametrize("depth_size", [(32, 24), (16, 12), (64, 48)])
def test_registered_geometry_and_no_object_origin(depth_size):
    w, h = depth_size
    sample = capture(depth=np.ones((h, w), np.float32))
    result = SpatialAnalyzer().analyze(sample, scene(sample))
    assert result.surface[0].status is EstimateStatus.AVAILABLE
    assert (
        result.positions[0].estimate.reference
        is SpatialReference.VISIBLE_POINTS_CENTROID
    )
    assert result.positions[0].estimate.xyz[2] == pytest.approx(1)
    assert not result.poses
    bad = replace(
        sample, depth_camera=replace(sample.depth_camera, fx=sample.depth_camera.fx * 2)
    )
    failed = SpatialAnalyzer().analyze(bad, scene(bad))
    assert failed.positions[0].reason == "spatial_registration_unavailable"


@pytest.mark.parametrize("fault", ["clock", "skew", "nan", "zero"])
def test_spatial_unavailable_does_not_erase_semantics(fault):
    sample = capture(depth=np.ones((24, 32), np.float32))
    if fault == "clock":
        sample = replace(
            sample, depth=replace(sample.depth, captured_at=CaptureTime("other", 0))
        )
    elif fault == "skew":
        sample = replace(
            sample,
            depth=replace(sample.depth, captured_at=CaptureTime("clock", 10_000_000)),
        )
    else:
        sample = replace(
            sample,
            depth=replace(
                sample.depth, data=np.full((24, 32), np.nan if fault == "nan" else 0)
            ),
        )
    semantic = scene(sample)
    result = SpatialAnalyzer().analyze(sample, semantic)
    assert result.positions[0].status is EstimateStatus.UNAVAILABLE
    assert len(semantic.objects) == 1


def test_foreground_outliers_and_custom_projector():
    depth = np.full((24, 32), 3.0, np.float32)
    depth[7:18, 10:24] = 1.0
    depth[8, 11] = 50.0
    sample = capture(depth=depth)
    result = SpatialAnalyzer(limits=VisionLimits(max_roi_points=50)).analyze(
        sample, scene(sample)
    )
    assert result.positions[0].estimate.point_count <= 50
    assert result.positions[0].estimate.xyz[2] == pytest.approx(1)

    class Projector:
        def point_at(self, capture, point):
            return PointSample((0, 0, 1), "custom")

        def points_in(self, capture, region, *, max_points):
            return PointSet(
                tuple(
                    (x / 100, y / 100, 1 + x / 1000) for x in range(4) for y in range(4)
                ),
                "custom",
            )

    custom = replace(
        sample,
        registration=None,
        depth_camera=replace(sample.depth_camera, frame_id="independent"),
    )
    result = SpatialAnalyzer(projector=Projector()).analyze(custom, scene(custom))
    assert result.positions[0].estimate.frame_id == "custom"


def test_pnp_subpixel_transform_direction_and_multi_solution():
    sample = capture(640, 480)
    camera = PinholeCamera(640, 480, 500, 510, 319.5, 239.5, "optical")
    object_points = np.array(
        [
            [-0.1, -0.1, 0],
            [0.1, -0.1, 0],
            [0.1, 0.1, 0],
            [-0.1, 0.1, 0],
            [0, 0, 0.2],
            [0.04, -0.03, 0.12],
        ],
        float,
    )
    rotation = np.array([0.15, 0.2, 0.1])
    translation = np.array([0.02, -0.04, 1.2])
    matrix = np.array([[500, 0, 319.5], [0, 510, 239.5], [0, 0, 1]], float)
    pixels = cv2.projectPoints(object_points, rotation, translation, matrix, None)[
        0
    ].reshape(-1, 2)
    inputs = PnPCorrespondences(
        sample.ref,
        "a",
        "object",
        tuple(tuple(p) for p in object_points),
        tuple(Point2D((u + 0.5) / 640, (v + 0.5) / 480) for u, v in pixels),
    )
    # Public inputs are Python scalars; the caller can use any CPU camera library.
    inputs = replace(
        inputs, object_points=tuple(tuple(float(x) for x in p) for p in object_points)
    )
    pose = solve_pnp(inputs, camera)
    assert pose.status is EstimateStatus.AVAILABLE
    assert pose.estimate.position == pytest.approx(translation, abs=1e-6)
    assert pose.estimate.reprojection_error_px < 1e-5
    expected_rotation = cv2.Rodrigues(rotation)[0]
    from roboagent.interaction.vision.geometry import rotation_quaternion

    assert pose.estimate.orientation_wxyz == pytest.approx(
        rotation_quaternion(expected_rotation), abs=1e-6
    )
    bad = replace(inputs, object_points=((0.0, 0.0, 0.0),) * 6)
    assert solve_pnp(bad, camera).status is EstimateStatus.UNAVAILABLE


def test_pose_transform_preserves_object_binding():
    async def check():
        sample = capture()
        pose = PoseResult(
            sample.ref,
            "a",
            EstimateStatus.AVAILABLE,
            PoseEstimate(
                sample.ref, "a", (0, 0, 1), (1, 0, 0, 0), "optical", "object", "host"
            ),
        )

        class Provider:
            async def transform_pose(self, estimate, *, target_frame, captured_at):
                assert captured_at == sample.captured_at
                return replace(estimate, position=(1, 0, 1), frame_id=target_frame)

        moved = await transform_pose(
            pose, Provider(), target_frame="arbitrary", captured_at=sample.captured_at
        )
        assert (
            moved.estimate.frame_id == "arbitrary"
            and moved.estimate.object_frame_id == "object"
        )

    asyncio.run(check())


@pytest.mark.parametrize(
    "size", [(320, 240), (853, 481), (1280, 720), (1920, 1080), (720, 1280)]
)
def test_cpu_geometry_multiple_source_resolutions(size):
    w, h = size
    sample = capture(w, h, depth=np.ones((h // 2, w // 2), np.float32))
    result = SpatialAnalyzer(limits=VisionLimits(max_roi_points=4096)).analyze(
        sample, scene(sample)
    )
    assert result.positions[0].status is EstimateStatus.AVAILABLE
    assert result.positions[0].estimate.xyz == pytest.approx((0, 0, 1), abs=0.002)
    assert result.positions[0].estimate.point_count <= 4096


def test_image_and_depth_resource_limits_fail_before_processing():
    sample = capture()
    with pytest.raises(VisionError, match="image_limit_exceeded"):
        ImageFrame(
            sample.image.data, "image/png", 32, 24, VisionLimits(max_decoded_pixels=20)
        )
    with pytest.raises(VisionError, match="depth_limit_exceeded"):
        DepthFrame(
            np.ones((24, 32)),
            CaptureTime("clock", 0),
            RECEIVED,
            VisionLimits(max_depth_bytes=16),
        )
    for kwargs in (
        {"max_objects": True},
        {"semantic_timeout_seconds": float("inf")},
        {"max_tracking_cache_bytes": 0},
    ):
        with pytest.raises(VisionError):
            VisionLimits(**kwargs)


def test_planar_pose_ambiguity_and_principal_axes_degeneracy():
    sample = capture(640, 480)
    camera = PinholeCamera(640, 480, 500, 500, 319.5, 239.5, "optical")
    points = ((-0.1, -0.1, 0.0), (0.1, -0.1, 0.0), (0.1, 0.1, 0.0), (-0.1, 0.1, 0.0))
    matrix = np.array([[500, 0, 319.5], [0, 500, 239.5], [0, 0, 1]], float)
    pixels = cv2.projectPoints(
        np.array(points),
        np.array([0.01, 0.01, 0.0]),
        np.array([0.0, 0.0, 2.0]),
        matrix,
        None,
    )[0].reshape(-1, 2)
    inputs = PnPCorrespondences(
        sample.ref,
        "a",
        "object",
        points,
        tuple(
            Point2D(float((u + 0.5) / 640), float((v + 0.5) / 480)) for u, v in pixels
        ),
    )
    result = solve_pnp(inputs, camera, policy=PnPPolicy(ambiguity_margin_px=1))
    assert result.status is EstimateStatus.AMBIGUOUS and result.estimate is None
    flat = capture(32, 32, depth=np.ones((32, 32), np.float32))
    axes = SpatialAnalyzer().analyze(flat, scene(flat)).orientations[0]
    assert axes.status is EstimateStatus.AMBIGUOUS and axes.estimate is None
