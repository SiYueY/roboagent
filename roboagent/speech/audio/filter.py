"""Input audio filters used before VAD and ASR."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..types import AudioChunk, AudioFormat


class AudioFilter(Protocol):
    """Stateful PCM filter with explicit lifecycle and tail flushing."""

    async def start(self, format: AudioFormat) -> None: ...
    async def process(self, audio: AudioChunk) -> Sequence[AudioChunk]: ...
    async def flush(self) -> Sequence[AudioChunk]: ...
    async def close(self) -> None: ...


class PassthroughAudioFilter:
    """No-op filter used in resource-constrained deployments and tests."""

    async def start(self, format: AudioFormat) -> None:
        self.format = format

    async def process(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        return (audio,)

    async def flush(self) -> Sequence[AudioChunk]:
        return ()

    async def close(self) -> None:
        return None
