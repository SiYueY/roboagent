"""Bounded temporal lookup for direct interaction, with no camera ownership."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from time import monotonic

from roboagent.message import MediaLimits
from .content import image_content_from_frame
from .types import VisionFrame


def _duration(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Age must be finite and non-negative.")


class VisionBuffer:
    def __init__(
        self,
        *,
        max_frames: int = 16,
        max_age: float = 5.0,
        max_bytes: int = 32 * 1024 * 1024,
        media_limits: MediaLimits | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if any(
            type(value) is not int or value < 1 for value in (max_frames, max_bytes)
        ):
            raise ValueError("Vision buffer limits must be positive integers.")
        _duration(max_age)
        self._frames: deque[VisionFrame] = deque(maxlen=max_frames)
        self._max_age = max_age
        self._max_bytes = max_bytes
        self._limits = media_limits or MediaLimits()
        self._clock = clock

    def _prune(self) -> None:
        now = self._clock()
        self._frames = deque(
            (
                frame
                for frame in self._frames
                if 0 <= now - frame.captured_at <= self._max_age
            ),
            maxlen=self._frames.maxlen,
        )

    def push(self, frame: VisionFrame) -> None:
        image_content_from_frame(frame, limits=self._limits)
        if len(frame.data) > self._max_bytes:
            raise ValueError("Frame exceeds buffer byte limit.")
        self._prune()
        self._frames = deque(
            sorted((*self._frames, frame), key=lambda item: item.captured_at),
            maxlen=self._frames.maxlen,
        )
        self._prune()
        while sum(len(item.data) for item in self._frames) > self._max_bytes:
            self._frames.popleft()

    def latest(self) -> VisionFrame | None:
        self._prune()
        return max(self._frames, key=lambda frame: frame.captured_at, default=None)

    def nearest(
        self, timestamp: float, *, max_age: float | None = None
    ) -> VisionFrame | None:
        _duration(timestamp)
        if max_age is not None:
            _duration(max_age)
        self._prune()
        frame = min(
            self._frames,
            key=lambda item: (abs(item.captured_at - timestamp), -item.captured_at),
            default=None,
        )
        return (
            frame
            if frame is not None
            and (max_age is None or abs(frame.captured_at - timestamp) <= max_age)
            else None
        )

    def clear(self) -> None:
        self._frames.clear()
