"""Vision device output types."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True, slots=True)
class VisionFrame:
    data: bytes
    mime_type: str
    width: int
    height: int
    timestamp: float = field(default_factory=monotonic)
    source: str | None = None
