"""Host-bound input retention; never runs inference or advances tracking."""

from collections.abc import Callable
from .types import (
    CaptureTime,
    SpatialCapture,
    VisualCapture,
    VisionError,
    VisionLimits,
    integer,
    text_id,
)
from .image import capture_digest


class VisualBuffer:
    def __init__(
        self,
        *,
        stream_id: str,
        clock: Callable[[str], CaptureTime | None],
        limits: VisionLimits | None = None,
    ):
        text_id(stream_id)
        if not callable(clock):
            raise VisionError("vision_input_invalid")
        self.stream_id = stream_id
        self.clock = clock
        self.limits = limits or VisionLimits()
        self.clock_id: str | None = None
        self._captures: dict[int, VisualCapture | SpatialCapture] = {}

    def activate_epoch(self, clock_id: str) -> None:
        text_id(clock_id)
        if self.clock_id != clock_id:
            self.clear()
            self.clock_id = clock_id

    def clear(self) -> None:
        self._captures.clear()

    def _now(self) -> CaptureTime:
        now = self.clock(self.clock_id or "")
        if not isinstance(now, CaptureTime) or now.clock_id != self.clock_id:
            raise VisionError("buffer_clock_unavailable")
        return now

    def _prune(self) -> None:
        now = self._now().nanoseconds
        self._captures = {
            seq: c
            for seq, c in self._captures.items()
            if 0
            <= now - self._visual(c).captured_at.nanoseconds
            <= self.limits.max_buffer_age_ns
        }

    @staticmethod
    def _visual(capture):
        return capture.visual if isinstance(capture, SpatialCapture) else capture

    def push(self, capture: VisualCapture | SpatialCapture) -> None:
        visual = self._visual(capture)
        if (
            visual.stream_id != self.stream_id
            or visual.captured_at.clock_id != self.clock_id
        ):
            raise VisionError("buffer_epoch_mismatch")
        if visual.captured_at.nanoseconds > self._now().nanoseconds:
            raise VisionError("buffer_future_capture")
        self._prune()
        existing = self._captures.get(visual.sequence)
        if existing is not None:
            if existing.ref != capture.ref or capture_digest(
                existing
            ) != capture_digest(capture):
                raise VisionError("capture_content_conflict")
            return

        def size(c):
            return len(self._visual(c).image.data) + (
                c.depth.data.nbytes if isinstance(c, SpatialCapture) else 0
            )

        if size(capture) > self.limits.max_buffer_bytes:
            raise VisionError("buffer_limit_exceeded")
        self._captures[visual.sequence] = capture
        self._prune()
        while (
            len(self._captures) > self.limits.max_buffer_frames
            or sum(size(c) for c in self._captures.values())
            > self.limits.max_buffer_bytes
        ):
            del self._captures[min(self._captures)]

    def latest(self) -> VisualCapture | SpatialCapture | None:
        self._prune()
        return self._captures[max(self._captures)] if self._captures else None

    def nearest(self, time: CaptureTime, *, max_delta_ns: int | None = None):
        if time.clock_id != self.clock_id:
            raise VisionError("buffer_epoch_mismatch")
        if max_delta_ns is not None:
            integer(max_delta_ns)
        self._prune()
        best = min(
            self._captures.values(),
            key=lambda c: (
                abs(self._visual(c).captured_at.nanoseconds - time.nanoseconds),
                -c.ref.sequence,
            ),
            default=None,
        )
        if best is not None and (
            max_delta_ns is None
            or abs(self._visual(best).captured_at.nanoseconds - time.nanoseconds)
            <= max_delta_ns
        ):
            return best
        return None
