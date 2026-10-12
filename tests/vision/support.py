from datetime import datetime, timezone

import numpy as np
import cv2

from roboagent.interaction.vision import (
    Box2D,
    CaptureTime,
    DepthFrame,
    ImageFrame,
    PinholeCamera,
    Point2D,
    RegisteredDepthGeometry,
    SpatialCapture,
    VisualCapture,
    VisualDetection,
    VisualScene,
)

RECEIVED = datetime(2026, 10, 12, tzinfo=timezone.utc)


def capture(width=32, height=24, *, sequence=0, clock="clock", depth=None):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    ok, data = cv2.imencode(".png", image)
    assert ok
    time = CaptureTime(clock, sequence * 1_000_000)
    visual = VisualCapture(
        "camera",
        str(sequence),
        sequence,
        ImageFrame(data.tobytes(), "image/png", width, height),
        time,
        RECEIVED,
        "optical",
    )
    if depth is None:
        return visual
    h, w = depth.shape
    camera = PinholeCamera(
        width,
        height,
        float(width),
        float(height),
        (width - 1) / 2,
        (height - 1) / 2,
        "optical",
    )
    return SpatialCapture(
        visual,
        camera,
        DepthFrame(depth, time, RECEIVED),
        camera.resized(w, h),
        RegisteredDepthGeometry("calibration"),
    )


def detection(id="a", *, x0=0.1, x1=0.9, color="red", category="object"):
    return VisualDetection(
        id,
        category,
        Box2D(x0, 0.1, x1, 0.9),
        Point2D((x0 + x1) / 2, 0.5),
        {"color": color},
    )


def scene(sample, objects=None, relations=()):
    ref = sample.ref
    return VisualScene(
        ref.stream_id,
        ref.clock_id,
        ref.capture_id,
        ref.sequence,
        tuple(objects if objects is not None else (detection(),)),
        tuple(relations),
    )
