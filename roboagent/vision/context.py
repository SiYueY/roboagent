"""Latest-frame context for an application conversation."""
from __future__ import annotations

from .types import VisionFrame


class VisionContext:
    def __init__(self) -> None:
        self._latest: VisionFrame | None = None

    def update(self, frame: VisionFrame) -> None:
        self._latest = frame

    def latest(self) -> VisionFrame | None:
        return self._latest

    def clear(self) -> None:
        self._latest = None
