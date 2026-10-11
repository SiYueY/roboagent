"""Validated frame conversion reuses canonical message media contracts."""

from roboagent.message import BytesSource, ImageContent, MediaLimits, ProtocolError
from .types import VisionFrame


def image_content_from_frame(
    frame: VisionFrame, *, limits: MediaLimits | None = None
) -> ImageContent:
    if not isinstance(frame, VisionFrame):
        raise TypeError("frame must be VisionFrame.")
    if limits is not None and not isinstance(limits, MediaLimits):
        raise TypeError("limits must be MediaLimits.")
    limits = limits or MediaLimits()
    if len(frame.data) > limits.max_inline_bytes:
        raise ProtocolError("Vision frame exceeds media size limit.")
    return ImageContent(BytesSource(frame.data), frame.mime_type)
