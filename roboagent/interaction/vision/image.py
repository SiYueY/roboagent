"""Validated image boundary and lazy CPU dependencies."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from datetime import datetime
from hashlib import sha256
from typing import Any
from collections.abc import Mapping
from numbers import Real
import struct

from roboagent.message import (
    BytesSource,
    ImageContent,
    MediaLimits,
    canonical_json_digest,
)
from .types import (
    ImageFrame,
    SpatialCapture,
    VisualCapture,
    VisionError,
    VisionLimits,
    integer,
)


def dependencies():
    try:
        import numpy as np
        import cv2
    except ImportError as exc:
        raise VisionError("vision_dependencies_unavailable") from exc
    return np, cv2


def image_dimensions(data: bytes, mime: str) -> tuple[int, int]:
    if (
        mime == "image/png"
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 24
        and data[12:16] == b"IHDR"
    ):
        return struct.unpack(">II", data[16:24])
    if mime == "image/jpeg" and data.startswith(b"\xff\xd8"):
        index = 2
        while index < len(data):
            if data[index] != 255:
                break
            while index < len(data) and data[index] == 255:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 1 or 0xD0 <= marker <= 0xD8:
                continue
            if index + 2 > len(data):
                break
            size = int.from_bytes(data[index : index + 2], "big")
            if size < 2 or index + size > len(data):
                break
            if (
                marker
                in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                )
                and size >= 8
            ):
                return int.from_bytes(
                    data[index + 5 : index + 7], "big"
                ), int.from_bytes(data[index + 3 : index + 5], "big")
            index += size
    raise VisionError("image_invalid")


def validate_image(
    data: bytes, mime: str, width: int, height: int, limits: VisionLimits
) -> None:
    integer(width, minimum=1)
    integer(height, minimum=1)
    if type(data) is not bytes or not data:
        raise VisionError("image_invalid")
    if (
        len(data) > limits.max_encoded_image_bytes
        or width * height > limits.max_decoded_pixels
    ):
        raise VisionError("image_limit_exceeded")
    actual = image_dimensions(data, mime)
    if actual[0] * actual[1] > limits.max_decoded_pixels:
        raise VisionError("image_limit_exceeded")
    if actual != (width, height):
        raise VisionError("image_dimension_mismatch")
    np, cv = dependencies()
    try:
        decoded = cv.imdecode(np.frombuffer(data, np.uint8), cv.IMREAD_UNCHANGED)
    except cv.error as exc:
        raise VisionError("image_invalid") from exc
    if decoded is None or decoded.shape[:2] != (height, width):
        raise VisionError("image_invalid")


def image_content(
    frame: ImageFrame, *, limits: MediaLimits | None = None
) -> ImageContent:
    if not isinstance(frame, ImageFrame):
        raise VisionError("image_invalid")
    if len(frame.data) > (limits or MediaLimits()).max_inline_bytes:
        raise VisionError("image_limit_exceeded")
    return ImageContent(BytesSource(frame.data), frame.mime_type)


def to_data(value: Any) -> Any:
    """Encode only bounded derived data; raw arrays/media never cross JSON boundaries."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_data(getattr(value, f.name))
            for f in fields(value)
            if f.name != "limits"
        }
    if isinstance(value, (tuple, list)):
        return [to_data(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(to_data(v) for v in value)
    if isinstance(value, Mapping):
        return {k: to_data(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Real):
        return float(value)
    raise VisionError("vision_serialization_invalid")


def capture_metadata(capture: VisualCapture | SpatialCapture) -> dict:
    visual = capture.visual if isinstance(capture, SpatialCapture) else capture
    data = {
        "capture_ref": to_data(visual.ref),
        "observed_at": to_data(visual.captured_at),
        "received_at": to_data(visual.received_at),
        "parent_ref": to_data(visual.parent_ref),
        "frame_id": visual.frame_id,
        "image": {
            "mime_type": visual.image.mime_type,
            "width": visual.image.width,
            "height": visual.image.height,
            "size": len(visual.image.data),
            "digest": sha256(visual.image.data).hexdigest(),
        },
    }
    if isinstance(capture, SpatialCapture):
        data["depth"] = {
            "shape": list(capture.depth.data.shape),
            "dtype": "float32",
            "unit": "metre-Z",
            "observed_at": to_data(capture.depth.captured_at),
            "received_at": to_data(capture.depth.received_at),
            "digest": sha256(capture.depth.data.tobytes()).hexdigest(),
        }
        data["visual_camera"] = to_data(capture.visual_camera)
        data["depth_camera"] = to_data(capture.depth_camera)
        data["registration"] = to_data(capture.registration)
        data["calibration_metadata"] = capture.calibration_metadata
    return data


def capture_digest(capture: VisualCapture | SpatialCapture) -> str:
    return canonical_json_digest(capture_metadata(capture))
