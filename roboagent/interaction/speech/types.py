"""Public media data contracts for the speech runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int
    channels: int
    sample_width: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width <= 0:
            raise ValueError("AudioFormat values must be positive.")


DEFAULT_INPUT_FORMAT = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)
DEFAULT_OUTPUT_FORMAT = AudioFormat(sample_rate=24_000, channels=1, sample_width=2)


@dataclass(frozen=True, slots=True)
class AudioChunk:
    data: bytes
    format: AudioFormat
    # Monotonic ordering is more important than wall-clock accuracy here.  A
    # transport may provide its own timestamp; local transports use creation
    # time as a useful diagnostic fallback.
    timestamp: float = field(default_factory=monotonic)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    final: bool
