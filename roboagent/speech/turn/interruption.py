"""Output-aware interruption policy, independent from endpointing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterruptionDecision:
    candidate: bool = False
    # ``confirmed`` here means the acoustic candidate has lasted long enough
    # to open an ASR confirmation window.  SpeechSession only cancels after
    # it sees meaningful ASR text from that window.
    confirmed: bool = False
    false_interruption: bool = False


class InterruptionDetector:
    def __init__(self, *, enabled: bool = True, min_duration_ms: int = 250,
                 min_confidence: float = 0.55, min_volume: float = 0.003) -> None:
        self.enabled = enabled
        self.min_duration_ms = min_duration_ms
        self.min_confidence = min_confidence
        self.min_volume = min_volume
        self._candidate_ms = 0.0
        self._was_candidate = False

    def update(self, *, speaking: bool, confidence: float, level: float,
               output_active: bool, duration_ms: float) -> InterruptionDecision:
        if not self.enabled or not output_active:
            false = self._was_candidate
            self.reset()
            return InterruptionDecision(false_interruption=false)
        candidate = speaking and confidence >= self.min_confidence and level >= self.min_volume
        if not candidate:
            false = self._was_candidate
            self.reset()
            return InterruptionDecision(false_interruption=false)
        self._was_candidate = True
        self._candidate_ms += duration_ms
        return InterruptionDecision(candidate=True, confirmed=self._candidate_ms >= self.min_duration_ms)

    def reset(self) -> None:
        self._candidate_ms = 0.0
        self._was_candidate = False
