"""RNNoise-backed server-side speech denoiser.

The dependency is optional at import time so text-only RoboAgent installations
remain usable.  A configured required filter fails clearly during ``start``.
"""
from __future__ import annotations

from collections.abc import Sequence
import logging

from ..errors import SpeechConfigurationError
from ..types import AudioChunk, AudioFormat

logger = logging.getLogger(__name__)


class RNNoiseProcessor:
    """Process capture PCM with RNNoise; render PCM passes through unchanged."""

    def __init__(self, *, required: bool = False, quality: str = "QQ") -> None:
        self.required = required
        self.quality = quality
        self._format: AudioFormat | None = None
        self._engine = None
        self._np = None
        self._soxr = None
        self._enabled = False
        self.speech_probability: float | None = None

    async def start(self, capture_format: AudioFormat, render_format: AudioFormat | None = None) -> None:
        self._format = capture_format
        if capture_format.channels != 1 or capture_format.sample_width != 2:
            raise SpeechConfigurationError("RNNoise requires mono PCM16 input.")
        try:
            import numpy as np
            import soxr
            from pyrnnoise import RNNoise
            self._np, self._soxr = np, soxr
            self._engine = RNNoise(sample_rate=48_000)
            self._enabled = True
        except Exception as exc:
            self._enabled = False
            message = "RNNoise is unavailable; using unfiltered input. Install the speech extra."
            if self.required:
                raise SpeechConfigurationError(message) from exc
            logger.warning("%s (%s)", message, exc)

    def _resample(self, data: bytes, source_rate: int, target_rate: int) -> bytes:
        if source_rate == target_rate:
            return data
        assert self._np is not None and self._soxr is not None
        samples = self._np.frombuffer(data, dtype=self._np.int16)
        if not len(samples):
            return b""
        output = self._soxr.resample(samples, source_rate, target_rate, quality=self.quality)
        return self._np.clip(output, -32768, 32767).astype(self._np.int16).tobytes()

    async def process_capture(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        if not self._enabled or self._engine is None or self._format is None:
            return (audio,)
        try:
            input_48k = self._resample(audio.data, audio.format.sample_rate, 48_000)
            samples = self._np.frombuffer(input_48k, dtype=self._np.int16)
            output_parts = []
            probabilities = []
            for speech_probability, denoised in self._engine.denoise_chunk(samples):
                probabilities.append(float(self._np.asarray(speech_probability).mean()))
                part = self._np.asarray(denoised).squeeze()
                if self._np.issubdtype(part.dtype, self._np.floating):
                    part = part * 32767
                output_parts.append(self._np.clip(part, -32768, 32767).astype(self._np.int16))
            if not output_parts:
                return ()
            self.speech_probability = sum(probabilities) / len(probabilities) if probabilities else None
            output_48k = self._np.concatenate(output_parts).tobytes()
            data = self._resample(output_48k, 48_000, audio.format.sample_rate)
            return (AudioChunk(data, audio.format, audio.timestamp),) if data else ()
        except Exception as exc:
            if self.required:
                raise SpeechConfigurationError("RNNoise filtering failed.") from exc
            logger.warning("RNNoise failed; bypassing this frame: %s", exc)
            return (audio,)

    async def process_render(self, audio: AudioChunk) -> Sequence[AudioChunk]:
        return (audio,)

    async def flush_capture(self) -> Sequence[AudioChunk]:
        # pyrnnoise owns incomplete native frames and does not expose an
        # end-of-stream flush API. Dropping an incomplete <10 ms tail is safer
        # than padding and injecting non-user audio into ASR.
        return ()

    async def close(self) -> None:
        self._engine = None
        self._enabled = False
        self.speech_probability = None
