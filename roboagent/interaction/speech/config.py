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
    # Local endpointing is authoritative. This is only a provider-side safety
    # net for a lost local completion signal.
    silence_ms: int = Field(default=800, ge=200, le=6000)
    speech_noise_threshold: float = Field(default=0.2, ge=-1.0, le=1.0)
    heartbeat: bool = True
    # Keep one streaming recognition task alive across local turns.  The
    # service emits sentence-final results while audio continues; stop only
    # when the SpeechSession itself closes or the socket fails.
    persistent: bool = True


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
    chunk_chars: int = Field(default=48, ge=8, le=256)

    @model_validator(mode="after")
    def validate_chunk_sizes(self) -> "DashScopeTTSConfig":
        if self.first_chunk_chars > self.chunk_chars:
            raise ValueError("tts.first_chunk_chars must not exceed tts.chunk_chars")
        return self


class WebRTCAudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    echo_cancellation: bool = True
    noise_suppression: bool = True
    auto_gain_control: bool = True
    high_pass_filter: bool = True
    stream_delay_ms: int = Field(default=60, ge=0, le=2_000)
    ns_level: int = Field(default=1, ge=0, le=3)


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: Literal["rnnoise", "passthrough", "webrtc"] = "rnnoise"
    required: bool = False
    resampler_quality: Literal["QQ", "LQ", "MQ", "HQ", "VHQ"] = "QQ"
    webrtc: WebRTCAudioConfig = Field(default_factory=WebRTCAudioConfig)


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


class InterruptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # This opens a candidate ASR probe.  It is not a cancellation by itself:
    # an intelligible ASR partial/final confirms the interruption.
    min_duration_ms: int = Field(default=300, ge=100, le=5000)
    min_confidence: float = Field(default=0.55, ge=0, le=1)
    min_volume: float = Field(default=0.003, ge=0, le=1)


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    silence_ms: int = Field(default=400, ge=100, le=6000)
    max_duration_ms: int = Field(default=20_000, ge=1000, le=60000)
    idle_timeout_ms: int = Field(default=1000, ge=100, le=10_000)
    min_speech_ms: int = Field(default=300, ge=0, le=5000)
    interruption: InterruptionConfig = Field(default_factory=InterruptionConfig)


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["alsa"] = "alsa"
    input_device: str = "default"
    output_device: str = "default"
    capture_sample_rate: int = Field(default=48_000, ge=8_000, le=96_000)
    playback_sample_rate: int = Field(default=48_000, ge=8_000, le=96_000)
    channels: int = Field(default=1, ge=1, le=2)
    period_ms: int = Field(default=20, ge=10, le=100)
    buffer_ms: int = Field(default=80, ge=20, le=1_000)


class SpeechConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asr: DashScopeASRConfig = Field(default_factory=DashScopeASRConfig)
    tts: DashScopeTTSConfig = Field(default_factory=DashScopeTTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    turn: TurnConfig = Field(default_factory=TurnConfig)
    device: DeviceConfig | None = None
    mode: Literal["pipeline", "realtime"] = "pipeline"
    diagnostics: bool = True
