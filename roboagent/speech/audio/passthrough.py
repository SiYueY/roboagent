"""No-op bidirectional processor."""
from __future__ import annotations

from collections.abc import Sequence

from ..types import AudioChunk, AudioFormat


class PassthroughAudioProcessor:
    speech_probability: float | None = None

    async def start(self, capture_format: AudioFormat, render_format: AudioFormat | None = None) -> None:
        self.capture_format, self.render_format = capture_format, render_format

    async def process_capture(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        return (audio,)

    async def process_render(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        return (audio,)

    async def flush_capture(self) -> Sequence[AudioChunk]:
        return ()

    async def close(self) -> None:
        return None
