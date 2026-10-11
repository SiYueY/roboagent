"""Contract for optional provider-format conversion."""
from __future__ import annotations

from typing import Protocol

from ..types import AudioChunk, AudioFormat


class AudioResampler(Protocol):
    def resample(self, audio: AudioChunk, target: AudioFormat) -> AudioChunk: ...
