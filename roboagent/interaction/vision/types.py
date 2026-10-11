"""Direct interaction frames use process-local monotonic timestamps."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import monotonic

from roboagent.message import ProtocolError, _mime


@dataclass(frozen=True, slots=True)
class VisionFrame:
    data: bytes
    mime_type: str
    width: int
    height: int
    captured_at: float = field(default_factory=monotonic)
    source: str | None = None

    def __post_init__(self) -> None:
        if type(self.data) is not bytes or not self.data:
            raise ProtocolError("VisionFrame.data must be non-empty bytes.")
        _mime(self.mime_type, "image/")
        if not isinstance(self.mime_type, str):
            raise ProtocolError("VisionFrame requires an image MIME type.")
        if any(
            type(value) is not int or value < 1 for value in (self.width, self.height)
        ):
            raise ProtocolError("VisionFrame dimensions must be positive integers.")
        if (
            type(self.captured_at) not in (float, int)
            or not math.isfinite(self.captured_at)
            or self.captured_at < 0
        ):
            raise ProtocolError(
                "VisionFrame.captured_at must be a monotonic timestamp."
            )
        if self.source is not None and not isinstance(self.source, str):
            raise ProtocolError("VisionFrame.source must be str or None.")
