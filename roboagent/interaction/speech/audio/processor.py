"""Bidirectional audio processing contracts."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..types import AudioChunk, AudioFormat


class AudioProcessor(Protocol):
    """Stateful processor for microphone capture and speaker render PCM."""

    speech_probability: float | None

    async def start(self, capture_format: AudioFormat, render_format: AudioFormat | None = None) -> None: ...
    async def process_capture(self, audio: AudioChunk) -> Sequence[AudioChunk]: ...
    async def process_render(self, audio: AudioChunk) -> Sequence[AudioChunk]: ...
    async def flush_capture(self) -> Sequence[AudioChunk]: ...
    async def close(self) -> None: ...
