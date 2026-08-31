"""Small PCM16 format conversion helper used at runtime boundaries."""
from __future__ import annotations

from ..errors import SpeechConfigurationError
from ..types import AudioChunk, AudioFormat


def convert_pcm16(audio: AudioChunk, target: AudioFormat, *, quality: str = "QQ") -> AudioChunk:
    """Down/up-mix and resample PCM16, preserving the chunk timestamp."""
    if audio.format == target:
        return audio
    if audio.format.sample_width != 2 or target.sample_width != 2:
        raise SpeechConfigurationError("Only PCM16 audio conversion is supported.")
    try:
        import numpy as np
        import soxr
    except Exception as exc:
        raise SpeechConfigurationError("PCM format conversion requires the speech extra.") from exc
    samples = np.frombuffer(audio.data, dtype=np.int16)
    if not len(samples):
        return AudioChunk(b"", target, audio.timestamp)
    if len(samples) % audio.format.channels:
        raise SpeechConfigurationError("PCM chunk is not aligned to its channel count.")
    frames = samples.reshape(-1, audio.format.channels).astype(np.float32)
    if target.channels == 1:
        frames = frames.mean(axis=1, keepdims=True)
    elif audio.format.channels == 1:
        frames = np.repeat(frames, target.channels, axis=1)
    elif audio.format.channels != target.channels:
        raise SpeechConfigurationError("Only mono and matching-channel PCM conversion is supported.")
    if audio.format.sample_rate != target.sample_rate:
        frames = soxr.resample(frames, audio.format.sample_rate, target.sample_rate, quality=quality)
        if frames.ndim == 1:
            frames = frames[:, None]
    data = np.clip(frames, -32768, 32767).astype(np.int16).reshape(-1).tobytes()
    return AudioChunk(data, target, audio.timestamp)
