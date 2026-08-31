"""Low-frequency state and lifecycle events emitted by speech sessions."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Literal


@dataclass(frozen=True, slots=True, kw_only=True)
class _SpeechEvent:
    timestamp: float = field(default_factory=monotonic)


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechStartedEvent(_SpeechEvent):
    type: Literal["speech.started"] = "speech.started"


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechStoppedEvent(_SpeechEvent):
    type: Literal["speech.stopped"] = "speech.stopped"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptPartialEvent(_SpeechEvent):
    text: str
    type: Literal["transcript.partial"] = "transcript.partial"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptFinalEvent(_SpeechEvent):
    text: str
    type: Literal["transcript.final"] = "transcript.final"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseStartedEvent(_SpeechEvent):
    type: Literal["response.started"] = "response.started"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseTextEvent(_SpeechEvent):
    delta: str
    type: Literal["response.delta"] = "response.delta"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseCompletedEvent(_SpeechEvent):
    type: Literal["response.completed"] = "response.completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioStartedEvent(_SpeechEvent):
    type: Literal["audio.started"] = "audio.started"


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioCompletedEvent(_SpeechEvent):
    type: Literal["audio.completed"] = "audio.completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class InterruptedEvent(_SpeechEvent):
    reason: str = "barge_in"
    type: Literal["interrupted"] = "interrupted"


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechErrorEvent(_SpeechEvent):
    error: str
    type: Literal["error"] = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechDiagnosticEvent(_SpeechEvent):
    """Optional aggregate telemetry; never includes audio or secrets."""
    level: float
    vad_state: str
    confidence: float = 0.0
    filter_latency_ms: float = 0.0
    dropped_frames: int = 0
    type: Literal["speech.diagnostics"] = "speech.diagnostics"


SpeechEvent = (SpeechStartedEvent | SpeechStoppedEvent | TranscriptPartialEvent | TranscriptFinalEvent |
               ResponseStartedEvent | ResponseTextEvent | ResponseCompletedEvent | AudioStartedEvent |
               AudioCompletedEvent | InterruptedEvent | SpeechErrorEvent | SpeechDiagnosticEvent)
