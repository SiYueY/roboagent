"""Voice activity detectors for filtered PCM16 microphone input."""

from __future__ import annotations

import audioop
import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ..errors import SpeechConfigurationError
from ..types import AudioChunk


class VADState(StrEnum):
    QUIET = "quiet"
    STARTING = "starting"
    SPEAKING = "speaking"
    STOPPING = "stopping"


class VAD(Protocol):
    state: VADState
    confidence: float
    level: float

    def process(self, audio: AudioChunk) -> bool: ...
    def reset(self) -> None: ...


class EnergyVAD:
    """Adaptive PCM16 RMS gate retained as an explicit fallback."""

    state = VADState.QUIET
    confidence = 0.0
    level = 0.0

    def __init__(
        self,
        threshold: float = 0.02,
        *,
        noise_multiplier: float = 3.0,
        calibration_frames: int = 25,
    ) -> None:
        self.threshold = threshold
        self.noise_multiplier = noise_multiplier
        self.calibration_frames = calibration_frames
        self._noise_floor = 0.0
        self._calibration_seen = 0

    def process(self, audio: AudioChunk) -> bool:
        if audio.format.sample_width != 2 or not audio.data:
            return False
        self.level = audioop.rms(audio.data, 2) / 32768
        if self._calibration_seen < self.calibration_frames:
            self._noise_floor = max(self._noise_floor, self.level)
            self._calibration_seen += 1
            self.confidence, self.state = 0.0, VADState.QUIET
            return False
        gate = max(self.threshold, self._noise_floor * self.noise_multiplier)
        speaking = self.level >= gate
        self.confidence = 1.0 if speaking else 0.0
        self.state = VADState.SPEAKING if speaking else VADState.QUIET
        if not speaking:
            self._noise_floor = self._noise_floor * 0.95 + self.level * 0.05
        return speaking

    def reset(self) -> None:
        self._noise_floor = 0.0
        self._calibration_seen = 0
        self.state = VADState.QUIET


class SileroVAD:
    """ONNX Silero VAD with start/stop debounce and an energy floor."""

    def __init__(
        self,
        *,
        confidence: float = 0.5,
        min_volume: float = 0.003,
        start_ms: int = 160,
        stop_ms: int = 400,
        model_path: str | None = None,
        required: bool = False,
    ) -> None:
        self.threshold, self.min_volume = confidence, min_volume
        self.start_ms, self.stop_ms = start_ms, stop_ms
        self.model_path, self.required = model_path, required
        self.state = VADState.QUIET
        self.confidence = self.level = 0.0
        self._session = self._np = None
        self._buffer = bytearray()
        self._state = self._context = None
        self._start_seen_ms = self._stop_seen_ms = 0.0
        self._available = False
        self._external_confidence: float | None = None

    def start(self) -> None:
        """Load once per speech session; falls back only when not required."""
        try:
            import numpy as np
            import onnxruntime as ort

            model = self.model_path or os.getenv("ROBOAGENT_SILERO_VAD_MODEL")
            if not model:
                model = str(Path(__file__).with_name("data") / "silero_vad.onnx")
            if not Path(model).is_file():
                raise FileNotFoundError(f"Silero ONNX model not found: {model}")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(
                model, providers=["CPUExecutionProvider"], sess_options=opts
            )
            self._np = np
            self._reset_model_state()
            self._available = True
            logging.getLogger(__name__).info(
                "Silero VAD loaded from %s (confidence >= %.2f, volume >= %.3f)",
                model,
                self.threshold,
                self.min_volume,
            )
        except Exception as exc:
            self._available = False
            if self.required:
                raise SpeechConfigurationError(
                    "Silero VAD is configured as required but unavailable."
                ) from exc
            logging.getLogger(__name__).warning(
                "Silero VAD unavailable; using RNNoise speech confidence when available, otherwise energy gate: %s",
                exc,
            )

    def set_external_confidence(self, confidence: float | None) -> None:
        """Accept RNNoise's built-in speech probability when Silero is absent."""
        self._external_confidence = confidence

    def _reset_model_state(self) -> None:
        assert self._np is not None
        self._state = self._np.zeros((2, 1, 128), dtype="float32")
        self._context = self._np.zeros((1, 64), dtype="float32")

    def _confidence(self, data: bytes) -> float:
        assert self._np is not None and self._session is not None
        samples = (
            self._np.frombuffer(data, dtype=self._np.int16).astype(self._np.float32)
            / 32768.0
        )
        model_input = self._np.concatenate((self._context, samples[None, :]), axis=1)
        output, self._state = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": self._np.array(16000, dtype="int64"),
            },
        )
        self._context = model_input[:, -64:]
        return float(output[0][0])

    def process(self, audio: AudioChunk) -> bool:
        if (
            audio.format.sample_rate != 16_000
            or audio.format.sample_width != 2
            or audio.format.channels != 1
        ):
            raise SpeechConfigurationError(
                "Silero VAD requires 16 kHz mono PCM16 input."
            )
        self.level = audioop.rms(audio.data, 2) / 32768 if audio.data else 0.0
        if not self._available:
            self.confidence = self._external_confidence or 0.0
            speech = (
                self.confidence >= self.threshold
                if self._external_confidence is not None
                else self.level >= max(self.min_volume, 0.02)
            )
            self._advance(
                speech and self.level >= self.min_volume, len(audio.data) / 32
            )
            return self.state == VADState.SPEAKING
        self._buffer.extend(audio.data)
        while len(self._buffer) >= 1024:  # 512 samples / 32 ms at 16 kHz
            frame = bytes(self._buffer[:1024])
            del self._buffer[:1024]
            self.confidence = self._confidence(frame)
            self._advance(
                self.confidence >= self.threshold and self.level >= self.min_volume,
                32,
            )
        # Barge-in and ASR creation must wait for a confirmed utterance, not
        # merely the first high-energy frame in the STARTING debounce window.
        return self.state == VADState.SPEAKING

    def _advance(self, raw_speaking: bool, duration_ms: float) -> bool:
        if raw_speaking:
            self._stop_seen_ms = 0
            if self.state in {VADState.QUIET, VADState.STOPPING}:
                self.state, self._start_seen_ms = (
                    VADState.STARTING,
                    self._start_seen_ms + duration_ms,
                )
            elif self.state == VADState.STARTING:
                self._start_seen_ms += duration_ms
            if self._start_seen_ms >= self.start_ms:
                self.state, self._start_seen_ms = VADState.SPEAKING, 0
        else:
            self._start_seen_ms = 0
            if self.state == VADState.STARTING:
                self.state = VADState.QUIET
            elif self.state in {VADState.SPEAKING, VADState.STOPPING}:
                self.state, self._stop_seen_ms = (
                    VADState.STOPPING,
                    self._stop_seen_ms + duration_ms,
                )
                if self._stop_seen_ms >= self.stop_ms:
                    self.state, self._stop_seen_ms = VADState.QUIET, 0
        return self.state in {VADState.STARTING, VADState.SPEAKING}

    def reset(self) -> None:
        self._buffer.clear()
        self.state, self.confidence, self.level = VADState.QUIET, 0.0, 0.0
        self._start_seen_ms = self._stop_seen_ms = 0
        if self._available:
            self._reset_model_state()
