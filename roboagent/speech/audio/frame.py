"""Canonical 20 ms PCM16 framing for VAD and ASR."""
from __future__ import annotations

from ..types import AudioChunk, AudioFormat


class AudioFrameAssembler:
    """Accumulate canonical PCM into exact fixed-duration frames."""

    def __init__(self, format: AudioFormat, *, frame_ms: int = 20) -> None:
        self.format = format
        self.frame_bytes = format.sample_rate * format.channels * format.sample_width * frame_ms // 1000
        self._data = bytearray()
        self._timestamp: float | None = None

    def push(self, audio: AudioChunk) -> tuple[AudioChunk, ...]:
        if audio.format != self.format:
            raise ValueError("AudioFrameAssembler requires canonical input format.")
        if self._timestamp is None:
            self._timestamp = audio.timestamp
        self._data.extend(audio.data)
        result: list[AudioChunk] = []
        while len(self._data) >= self.frame_bytes:
            timestamp = self._timestamp if self._timestamp is not None else audio.timestamp
            result.append(AudioChunk(bytes(self._data[:self.frame_bytes]), self.format, timestamp))
            del self._data[:self.frame_bytes]
            self._timestamp = timestamp + self.frame_bytes / (self.format.sample_rate * self.format.channels * self.format.sample_width)
        return tuple(result)

    def flush(self) -> tuple[AudioChunk, ...]:
        """Pad the final short capture so downstream consumers only see 20 ms frames."""
        if not self._data:
            return ()
        timestamp = self._timestamp
        data = bytes(self._data).ljust(self.frame_bytes, b"\0")
        self._data.clear()
        self._timestamp = None
        return (AudioChunk(data, self.format, timestamp if timestamp is not None else 0.0),)
