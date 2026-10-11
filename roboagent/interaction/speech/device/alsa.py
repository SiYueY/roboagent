"""Optional ALSA PCM devices. Blocking driver calls run off the event loop."""
from __future__ import annotations

import asyncio

from ..errors import SpeechConfigurationError
from ..types import AudioChunk, AudioFormat


class _AlsaBase:
    def __init__(self, device: str, format: AudioFormat, period_ms: int = 20, buffer_ms: int = 80) -> None:
        self.device, self.format = device, format
        self.period_frames = max(1, format.sample_rate * period_ms // 1000)
        self.buffer_frames = max(self.period_frames, format.sample_rate * buffer_ms // 1000)
        self._pcm = None

    def _alsa(self):
        try:
            import alsaaudio
            return alsaaudio
        except Exception as exc:
            raise SpeechConfigurationError("ALSA audio requires `pip install roboagent[speech-alsa]` and libasound.") from exc

    def _configure(self, pcm) -> None:
        alsa = self._alsa()
        pcm.setchannels(self.format.channels)
        pcm.setrate(self.format.sample_rate)
        pcm.setformat(alsa.PCM_FORMAT_S16_LE)
        pcm.setperiodsize(self.period_frames)
        pcm.setbuffersize(self.buffer_frames)


class AlsaAudioInput(_AlsaBase):
    async def start(self) -> None:
        def open_pcm():
            alsa = self._alsa()
            pcm = alsa.PCM(type=alsa.PCM_CAPTURE, mode=alsa.PCM_NORMAL, device=self.device)
            self._configure(pcm)
            return pcm
        self._pcm = await asyncio.to_thread(open_pcm)

    async def read(self) -> AudioChunk:
        if self._pcm is None:
            raise RuntimeError("ALSA input has not started.")
        length, data = await asyncio.to_thread(self._pcm.read)
        return AudioChunk(data if length > 0 else b"", self.format)

    async def close(self) -> None:
        pcm, self._pcm = self._pcm, None
        if pcm is not None:
            await asyncio.to_thread(pcm.close)


class AlsaAudioOutput(_AlsaBase):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        def open_pcm():
            alsa = self._alsa()
            pcm = alsa.PCM(type=alsa.PCM_PLAYBACK, mode=alsa.PCM_NORMAL, device=self.device)
            self._configure(pcm)
            return pcm
        self._pcm = await asyncio.to_thread(open_pcm)

    async def write(self, audio: AudioChunk) -> None:
        if self._pcm is None:
            raise RuntimeError("ALSA output has not started.")
        async with self._write_lock:
            await asyncio.to_thread(self._pcm.write, audio.data)

    async def clear(self) -> None:
        if self._pcm is not None:
            async with self._write_lock:
                await asyncio.to_thread(self._pcm.drop)
                await asyncio.to_thread(self._pcm.prepare)

    async def close(self) -> None:
        pcm, self._pcm = self._pcm, None
        if pcm is not None:
            await asyncio.to_thread(pcm.close)
