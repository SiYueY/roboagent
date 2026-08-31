from __future__ import annotations

import asyncio
import unittest

from roboagent.speech.transport.local import LocalSpeechTransport
from roboagent.speech.types import AudioChunk, DEFAULT_INPUT_FORMAT


class _Input:
    def __init__(self) -> None:
        self.closed = False

    async def start(self) -> None:
        return None

    async def read(self) -> AudioChunk:
        await asyncio.sleep(0)
        return AudioChunk(b"x", DEFAULT_INPUT_FORMAT)

    async def close(self) -> None:
        self.closed = True


class _Output:
    def __init__(self) -> None:
        self.closed = self.cleared = False
        self.written: list[bytes] = []

    async def start(self) -> None:
        return None

    async def write(self, audio: AudioChunk) -> None:
        self.written.append(audio.data)

    async def clear(self) -> None:
        self.cleared = True

    async def close(self) -> None:
        self.closed = True


class LocalSpeechTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_queue_clear_and_close_devices(self) -> None:
        audio_input, audio_output = _Input(), _Output()
        transport = LocalSpeechTransport(audio_input=audio_input, audio_output=audio_output)
        await transport._start()
        await transport.send_audio(AudioChunk(b"one", DEFAULT_INPUT_FORMAT))
        await transport.clear_output()
        self.assertTrue(audio_output.cleared)
        await transport.close()
        self.assertTrue(audio_input.closed)
        self.assertTrue(audio_output.closed)

    async def test_render_observer_runs_only_in_playback_worker(self) -> None:
        audio_input, audio_output = _Input(), _Output()
        transport = LocalSpeechTransport(audio_input=audio_input, audio_output=audio_output)
        observed: list[bytes] = []
        transport.set_render_observer(lambda audio: observed.append(audio.data))
        await transport._start()
        await transport.send_audio(AudioChunk(b"one", DEFAULT_INPUT_FORMAT))
        for _ in range(10):
            if observed:
                break
            await asyncio.sleep(0)
        self.assertEqual(observed, [b"one"])
        self.assertGreaterEqual(transport.playback_queue_latency_ms(), 0.0)
        await transport.close()
