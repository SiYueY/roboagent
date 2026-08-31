"""Turn-level timing metrics for the realtime speech runtime."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(slots=True)
class SpeechMetrics:
    input_frames: int = 0
    dropped_frames: int = 0
    audio_process_ms: float = 0.0
    speech_started_at: float | None = None
    speech_stopped_at: float | None = None
    asr_first_partial_at: float | None = None
    asr_final_at: float | None = None
    agent_first_token_at: float | None = None
    tts_first_audio_at: float | None = None
    playback_started_at: float | None = None
    interruption_at: float | None = None
    interruption_candidate_at: float | None = None
    playback_queue_latency_ms: float = 0.0

    def mark(self, name: str) -> None:
        if getattr(self, name) is None:
            setattr(self, name, monotonic())

    def durations(self) -> dict[str, float]:
        stop = self.speech_stopped_at
        return {
            "asr_first_partial_ms": self._after(self.asr_first_partial_at, self.speech_started_at),
            "asr_final_ms": self._after(self.asr_final_at, stop),
            "agent_ttft_ms": self._after(self.agent_first_token_at, self.asr_final_at),
            "tts_ttfa_ms": self._after(self.tts_first_audio_at, self.agent_first_token_at),
            "e2e_turn_ms": self._after(self.playback_started_at or self.tts_first_audio_at, stop),
            "interruption_latency_ms": self._after(self.interruption_at, self.interruption_candidate_at),
        }

    @staticmethod
    def _after(end: float | None, start: float | None) -> float:
        return (end - start) * 1000 if end is not None and start is not None else 0.0
