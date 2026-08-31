"""Speech synthesis contract."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ..types import AudioChunk


class TTS(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]: ...
