"""Validated configuration for real-time speech services and local audio DSP."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DashScopeASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)
    provider: str = "dashscope"
    model: str = "qwen-audio-3.0-asr-flash-streaming"
    api_key: str | None = Field(default=None, alias="dashscope_api_key")
    workspace_id: str | None = None
    region: str = "beijing"
    language_hints: tuple[str, ...] = ("zh",)
    silence_ms: int = Field(default=1500, ge=200, le=6000)
    speech_noise_threshold: float = Field(default=0.2, ge=-1.0, le=1.0)
    heartbeat: bool = True


class DashScopeTTSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)
    provider: str = "dashscope"
    model: str = "qwen3-tts-flash-realtime"
    voice: str = "Cherry"
    api_key: str | None = Field(default=None, alias="dashscope_api_key")
    workspace_id: str | None = None
    region: str = "beijing"
    language: str = "Chinese"
    # DashScope's realtime SDK defaults to 50, which is too quiet on many
    # browser/mobile output paths. Let the provider adjust gain before PCM is
    # produced rather than amplifying already-quantized browser audio.
    volume: int = Field(default=100, ge=0, le=100)
    first_chunk_chars: int = Field(default=16, ge=4, le=128)
    chunk_chars: int = Field(default=32, ge=8, le=256)

    @model_validator(mode="after")
    def validate_chunk_sizes(self) -> "DashScopeTTSConfig":
        if self.first_chunk_chars > self.chunk_chars:
            raise ValueError("tts.first_chunk_chars must not exceed tts.chunk_chars")
        return self


class AudioFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["rnnoise", "passthrough", "krisp"] = "rnnoise"
    required: bool = False
    resampler_quality: Literal["QQ", "LQ", "MQ", "HQ", "VHQ"] = "QQ"
    model_path: str | None = None
    api_key: str | None = None


class VADConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["silero", "energy"] = "silero"
    required: bool = False
    # Silero's published operating point is 0.5.  The former 0.65 / 0.008
    # pair was too strict for browser microphones with AGC intentionally off:
    # the client streamed correctly but normal speech never reached a turn.
    confidence: float = Field(default=0.5, ge=0, le=1)
    min_volume: float = Field(default=0.003, ge=0, le=1)
    start_ms: int = Field(default=160, ge=32, le=3000)
    stop_ms: int = Field(default=400, ge=32, le=6000)
    model_path: str | None = None
    threshold: float = Field(default=0.02, gt=0)
    noise_multiplier: float = Field(default=3.0, ge=1.0, le=20.0)
    calibration_ms: int = Field(default=500, ge=0, le=3000)


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    silence_ms: int = Field(default=700, ge=100, le=6000)
    max_duration_ms: int = Field(default=20_000, ge=1000, le=60000)
    idle_timeout_ms: int = Field(default=1000, ge=100, le=10_000)
    min_speech_ms: int = Field(default=300, ge=0, le=5000)
    # Output is especially susceptible to speaker echo. Barge-in therefore
    # needs a stronger and longer VAD confirmation than an ordinary turn.
    barge_in_ms: int = Field(default=400, ge=100, le=5000)
    barge_in_confidence: float = Field(default=0.60, ge=0, le=1)
    barge_in_min_volume: float = Field(default=0.004, ge=0, le=1)


class SpeechConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asr: DashScopeASRConfig = Field(default_factory=DashScopeASRConfig)
    tts: DashScopeTTSConfig = Field(default_factory=DashScopeTTSConfig)
    audio_filter: AudioFilterConfig = Field(default_factory=AudioFilterConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    turn: TurnConfig = Field(default_factory=TurnConfig)
    diagnostics: bool = True

    # Deprecated flat keys accepted for one release.
    vad_threshold: float | None = Field(default=None, exclude=True)
    vad_noise_multiplier: float | None = Field(default=None, exclude=True)
    vad_calibration_ms: int | None = Field(default=None, exclude=True)
    turn_silence_ms: int | None = Field(default=None, exclude=True)
    turn_max_duration_ms: int | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def map_legacy_audio_options(self) -> "SpeechConfig":
        if self.vad_threshold is not None:
            self.vad = self.vad.model_copy(update={"threshold": self.vad_threshold})
        if self.vad_noise_multiplier is not None:
            self.vad = self.vad.model_copy(update={"noise_multiplier": self.vad_noise_multiplier})
        if self.vad_calibration_ms is not None:
            self.vad = self.vad.model_copy(update={"calibration_ms": self.vad_calibration_ms})
        if self.turn_silence_ms is not None:
            self.turn = self.turn.model_copy(update={"silence_ms": self.turn_silence_ms})
        if self.turn_max_duration_ms is not None:
            self.turn = self.turn.model_copy(update={"max_duration_ms": self.turn_max_duration_ms})
        return self
