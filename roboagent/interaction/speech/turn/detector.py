"""Convert voice activity into speech-start and turn-complete signals."""
from __future__ import annotations

from time import monotonic


class TurnDetector:
    def __init__(self, silence_ms: int = 700, max_duration_ms: int = 20_000,
                 idle_timeout_ms: int = 1_000, min_speech_ms: int = 300) -> None:
        self.silence_seconds = silence_ms / 1000
        self.max_duration_seconds = max_duration_ms / 1000
        self.idle_timeout_seconds = idle_timeout_ms / 1000
        self.min_speech_seconds = min_speech_ms / 1000
        self._speaking = False
        self._quiet_since: float | None = None
        self._started_at: float | None = None
        self._last_audio_at: float | None = None
        self.last_turn_valid = False

    def update(self, speaking: bool) -> tuple[bool, bool]:
        now = monotonic()
        self._last_audio_at = now
        if speaking:
            started = not self._speaking
            self._speaking, self._quiet_since = True, None
            self._started_at = self._started_at or now
            if now - self._started_at >= self.max_duration_seconds:
                self._finish(now)
                return started, True
            return started, False
        if not self._speaking:
            return False, False
        self._quiet_since = self._quiet_since or now
        if now - self._quiet_since >= self.silence_seconds:
            self._finish(now)
            return False, True
        return False, False

    def idle(self) -> bool:
        """Force completion after microphone/network audio stops arriving."""
        if not self._speaking or self._last_audio_at is None:
            return False
        if monotonic() - self._last_audio_at < self.idle_timeout_seconds:
            return False
        self._finish(monotonic())
        return True

    def _finish(self, now: float) -> None:
        self.last_turn_valid = (
            self._started_at is not None and now - self._started_at >= self.min_speech_seconds
        )
        self.reset()

    def reset(self) -> None:
        self._speaking = False
        self._quiet_since = None
        self._started_at = None
        self._last_audio_at = None
