"""Bounded PCM chunk accumulation for short pre-roll and utterances."""
from __future__ import annotations

from collections import deque

from ..types import AudioChunk


class AudioBuffer:
    def __init__(self, *, max_bytes: int = 32_000) -> None:
        self.max_bytes = max_bytes
        self._chunks: deque[AudioChunk] = deque()
        self._size = 0

    def append(self, audio: AudioChunk) -> None:
        self._chunks.append(audio)
        self._size += len(audio.data)
        while self._size > self.max_bytes and self._chunks:
            self._size -= len(self._chunks.popleft().data)

    def chunks(self) -> tuple[AudioChunk, ...]:
        return tuple(self._chunks)

    def read(self) -> bytes:
        return b"".join(chunk.data for chunk in self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0
