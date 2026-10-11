"""Transport that composes native audio input and output devices."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from time import monotonic

from ..device.base import AudioInput, AudioOutput
from ..event import SpeechEvent
from ..types import AudioChunk

_STOP = object()


class LocalSpeechTransport:
    def __init__(self, *, audio_input: AudioInput, audio_output: AudioOutput, playback_queue_size: int = 12) -> None:
        self.audio_input, self.audio_output = audio_input, audio_output
        self._queue: asyncio.Queue[tuple[AudioChunk, float] | object] = asyncio.Queue(playback_queue_size)
        self._worker: asyncio.Task[None] | None = None
        self.events: list[SpeechEvent] = []
        self._closed = False
        self._render_observer = None
        self._playback_queue_latency_ms = 0.0
        self._first_playback_started_at: float | None = None

    def set_render_observer(self, observer) -> None:
        """Receive PCM only when the playback worker is about to write it."""
        self._render_observer = observer

    def playback_queue_latency_ms(self) -> float:
        return self._playback_queue_latency_ms

    def playback_started_at(self) -> float | None:
        return self._first_playback_started_at

    def reset_playback_metrics(self) -> None:
        self._first_playback_started_at = None
        self._playback_queue_latency_ms = 0.0

    async def _start(self) -> None:
        if self._worker is None:
            await self.audio_input.start()
            await self.audio_output.start()
            self._worker = asyncio.create_task(self._playback_worker())

    async def receive_audio(self) -> AsyncIterator[AudioChunk]:
        await self._start()
        while not self._closed:
            audio = await self.audio_input.read()
            if audio.data:
                yield audio

    async def send_audio(self, audio: AudioChunk) -> None:
        await self._start()
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait((audio, monotonic()))

    async def send_event(self, event: SpeechEvent) -> None:
        self.events.append(event)

    async def clear_output(self) -> None:
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        await self.audio_output.clear()

    async def _playback_worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            audio, queued_at = item
            self._playback_queue_latency_ms = (monotonic() - queued_at) * 1000
            if self._first_playback_started_at is None:
                self._first_playback_started_at = monotonic()
            if self._render_observer is not None:
                outcome = self._render_observer(audio)
                if asyncio.iscoroutine(outcome):
                    await outcome
            await self.audio_output.write(audio)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            if self._queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
            self._queue.put_nowait(_STOP)
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        await self.audio_input.close()
        await self.audio_output.close()
