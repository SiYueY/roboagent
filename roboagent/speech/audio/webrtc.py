"""Optional WebRTC AEC/NS/AGC audio processor."""
from __future__ import annotations

from collections.abc import Sequence

from ..errors import SpeechConfigurationError
from ..types import AudioChunk, AudioFormat
from .pcm import convert_pcm16


class WebRTCAudioProcessor:
    """AEC-aware processor using final speaker PCM as the far-end reference."""

    def __init__(self, *, echo_cancellation: bool = True, noise_suppression: bool = True,
                 auto_gain_control: bool = True, high_pass_filter: bool = True,
                 stream_delay_ms: int = 60, ns_level: int = 1) -> None:
        self.options = dict(echo_cancellation=echo_cancellation, noise_suppression=noise_suppression,
                            auto_gain_control=auto_gain_control, high_pass_filter=high_pass_filter,
                            stream_delay_ms=stream_delay_ms, ns_level=ns_level)
        self.speech_probability: float | None = None
        self.gain_db: float | None = None
        self._processor = None
        self._np = None
        self._capture_format: AudioFormat | None = None
        self._render_format: AudioFormat | None = None
        self._far = bytearray()

    async def start(self, capture_format: AudioFormat, render_format: AudioFormat | None = None) -> None:
        if capture_format.sample_width != 2 or capture_format.channels not in (1, 2):
            raise SpeechConfigurationError("WebRTC processor requires mono or stereo PCM16 capture.")
        if capture_format.sample_rate not in (16_000, 32_000, 48_000):
            raise SpeechConfigurationError("WebRTC processor supports 16, 32, or 48 kHz capture.")
        try:
            import numpy as np
            from pywebrtc_audio import AudioProcessor as WebRTCDSP
        except Exception as exc:
            raise SpeechConfigurationError("WebRTC DSP requires `pip install roboagent[speech-webrtc]`.") from exc
        self._np = np
        self._capture_format = capture_format
        self._render_format = render_format or capture_format
        self._processor = WebRTCDSP(sample_rate=capture_format.sample_rate, num_channels=capture_format.channels,
                                    **self.options)

    async def process_render(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        if self._capture_format is None or self._render_format is None:
            raise RuntimeError("Audio processor has not started.")
        rendered = convert_pcm16(audio, self._render_format)
        return (rendered,)

    def observe_render(self, audio: AudioChunk) -> None:
        """Register PCM at the point it is actually handed to the speaker."""
        if self._capture_format is None:
            raise RuntimeError("Audio processor has not started.")
        reference = convert_pcm16(audio, self._capture_format)
        self._far.extend(reference.data)

    async def process_capture(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        if self._processor is None or self._capture_format is None or self._np is None:
            raise RuntimeError("Audio processor has not started.")
        near = convert_pcm16(audio, self._capture_format)
        size = len(near.data)
        far = bytes(self._far[:size])
        del self._far[:size]
        if len(far) < size:
            far += b"\0" * (size - len(far))
        near_samples = self._np.frombuffer(near.data, dtype=self._np.int16)
        far_samples = self._np.frombuffer(far, dtype=self._np.int16)
        clean = self._processor.process(near_samples, far_samples if self.options["echo_cancellation"] else None)
        self.speech_probability = float(self._processor.speech_probability)
        try:
            self.gain_db = float(self._processor.gain_db)
        except RuntimeError:
            self.gain_db = None
        return (AudioChunk(clean.astype(self._np.int16, copy=False).tobytes(), self._capture_format, audio.timestamp),)

    async def flush_capture(self) -> Sequence[AudioChunk]:
        return ()

    async def close(self) -> None:
        self._far.clear()
        self._processor = self._np = None
        self.speech_probability = self.gain_db = None

    def reset(self) -> None:
        if self._processor is not None:
            self._processor.reset()
        self._far.clear()
