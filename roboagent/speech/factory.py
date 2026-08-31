"""Composition helpers for browser and native speech sessions."""
from __future__ import annotations

import logging

from .asr.dashscope import DashScopeASR
from .audio import EnergyVAD, PassthroughAudioProcessor, RNNoiseProcessor, SileroVAD, WebRTCAudioProcessor
from .config import SpeechConfig
from .device.alsa import AlsaAudioInput, AlsaAudioOutput
from .errors import SpeechConfigurationError
from .session import SpeechSession
from .text.segmenter import TextSegmenter
from .transport.local import LocalSpeechTransport
from .tts.dashscope import DashScopeTTS
from .turn.detector import TurnDetector
from .turn.interruption import InterruptionDetector
from .types import AudioFormat, DEFAULT_INPUT_FORMAT, DEFAULT_OUTPUT_FORMAT

logger = logging.getLogger(__name__)


def create_audio_processor(config: SpeechConfig):
    options = config.audio
    if options.processor == "passthrough":
        return PassthroughAudioProcessor()
    if options.processor == "rnnoise":
        return RNNoiseProcessor(required=options.required, quality=options.resampler_quality)
    if options.processor == "webrtc":
        return WebRTCAudioProcessor(**options.webrtc.model_dump())
    raise SpeechConfigurationError(f"Unsupported audio processor: {options.processor}")


def create_vad(config: SpeechConfig):
    options = config.vad
    if options.provider == "energy":
        return EnergyVAD(options.threshold, noise_multiplier=options.noise_multiplier,
                         calibration_frames=max(0, options.calibration_ms // 20))
    return SileroVAD(confidence=options.confidence, min_volume=options.min_volume,
                     start_ms=options.start_ms, stop_ms=options.stop_ms,
                     model_path=options.model_path, required=options.required)


def create_speech_session(*, agent_session, transport, config: SpeechConfig,
                          capture_format: AudioFormat = DEFAULT_INPUT_FORMAT,
                          render_format: AudioFormat = DEFAULT_OUTPUT_FORMAT) -> SpeechSession:
    if config.mode == "realtime":
        # The installed DashScope Python SDK exposes ASR and TTS realtime
        # clients, but not a stable full-duplex Qwen-Audio client surface.
        # Keep mode selection explicit and fail safe to the measured pipeline
        # instead of creating a half-connected session.
        logger.warning("speech.mode=realtime is unavailable in this runtime; falling back to pipeline")
    session = SpeechSession(
        agent_session=agent_session, transport=transport, asr=DashScopeASR(config.asr), tts=DashScopeTTS(config.tts),
        audio_processor=create_audio_processor(config), vad=create_vad(config),
        turn_detector=TurnDetector(config.turn.silence_ms, config.turn.max_duration_ms,
                                   config.turn.idle_timeout_ms, config.turn.min_speech_ms),
        interruption_detector=InterruptionDetector(**config.turn.interruption.model_dump()),
        segmenter=TextSegmenter(config.tts.chunk_chars, first_chunk_chars=config.tts.first_chunk_chars),
        capture_format=capture_format, render_format=render_format, diagnostics=config.diagnostics,
    )
    observer_setter = getattr(transport, "set_render_observer", None)
    observer = getattr(session.audio_processor, "observe_render", None)
    if observer_setter is not None and observer is not None:
        observer_setter(observer)
    return session


def create_local_transport(config: SpeechConfig) -> tuple[LocalSpeechTransport, AudioFormat, AudioFormat]:
    if config.device is None:
        raise SpeechConfigurationError("speech.device is required for local audio.")
    device = config.device
    capture = AudioFormat(device.capture_sample_rate, device.channels, 2)
    render = AudioFormat(device.playback_sample_rate, device.channels, 2)
    transport = LocalSpeechTransport(
        audio_input=AlsaAudioInput(device.input_device, capture, device.period_ms, device.buffer_ms),
        audio_output=AlsaAudioOutput(device.output_device, render, device.period_ms, device.buffer_ms),
    )
    return transport, capture, render
