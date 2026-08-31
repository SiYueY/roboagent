"""Turn streamed LLM deltas into synthesis-ready phrases."""
from __future__ import annotations


class TextSegmenter:
    """Build small first TTS chunks, then favour sentence-quality chunks.

    LLM subtitles may be emitted a token at a time, while a TTS provider needs
    a useful phrase.  A short first chunk limits time-to-first-audio; later
    chunks are longer to avoid choppy prosody and repeated provider commits.
    """

    def __init__(self, max_chars: int = 48, *, first_chunk_chars: int | None = None) -> None:
        first_chunk_chars = min(16, max_chars) if first_chunk_chars is None else first_chunk_chars
        if first_chunk_chars < 1 or max_chars < first_chunk_chars:
            raise ValueError("first_chunk_chars must be positive and no greater than max_chars.")
        self.max_chars = max_chars
        self.first_chunk_chars = first_chunk_chars
        self._buffer = ""
        self._sent_first_chunk = False

    def push(self, text: str) -> list[str]:
        self._buffer += text
        result: list[str] = []
        while self._buffer:
            endings = [self._buffer.find(mark) for mark in "。！？!?" if self._buffer.find(mark) >= 0]
            end = min(endings) + 1 if endings else 0
            target = self.max_chars if self._sent_first_chunk else self.first_chunk_chars
            if end == 0 and len(self._buffer) < target:
                break
            if end == 0:
                # A soft break avoids cutting most Chinese phrases in the
                # middle while still guaranteeing the first synthesis request.
                soft_breaks = [self._buffer.rfind(mark, 0, target + 1) for mark in "，、；：,;:"]
                end = max(soft_breaks) + 1
                end = end if end > 0 else target
            result.append(self._buffer[:end].strip())
            self._buffer = self._buffer[end:]
            self._sent_first_chunk = True
        return [item for item in result if item]

    def flush(self) -> str | None:
        result, self._buffer = self._buffer.strip(), ""
        return result or None

    def reset(self) -> None:
        self._buffer = ""
        self._sent_first_chunk = False
