from __future__ import annotations

import unittest
from collections.abc import AsyncIterator, Sequence
from importlib.util import find_spec

from roboagent.speech.audio.buffer import AudioBuffer
from roboagent.speech.audio.filter import PassthroughAudioFilter
from roboagent.speech.audio.rnnoise import RNNoiseFilter
from roboagent.speech.audio.vad import EnergyVAD, SileroVAD, VADState
from roboagent.speech.config import DashScopeTTSConfig
from roboagent.speech.session import SpeechSession
from roboagent.speech.text.segmenter import TextSegmenter
from roboagent.speech.turn.detector import TurnDetector
from roboagent.speech.types import AudioChunk, DEFAULT_INPUT_FORMAT


class SpeechPrimitiveTests(unittest.TestCase):
    def test_audio_buffer_is_bounded(self) -> None:
        buffer = AudioBuffer(max_bytes=4)
        buffer.append(AudioChunk(b"ab", DEFAULT_INPUT_FORMAT))
        buffer.append(AudioChunk(b"cd", DEFAULT_INPUT_FORMAT))
        buffer.append(AudioChunk(b"ef", DEFAULT_INPUT_FORMAT))
        self.assertEqual(buffer.read(), b"cdef")

    def test_audio_chunks_get_monotonic_timestamps_by_default(self) -> None:
        first = AudioChunk(b"", DEFAULT_INPUT_FORMAT)
        second = AudioChunk(b"", DEFAULT_INPUT_FORMAT)
        self.assertLessEqual(first.timestamp, second.timestamp)

    def test_tts_workspace_configuration_is_preserved(self) -> None:
        self.assertEqual(DashScopeTTSConfig(workspace_id="workspace-1").workspace_id, "workspace-1")

    def test_energy_vad_recognizes_pcm16_energy(self) -> None:
        vad = EnergyVAD(threshold=0.01, calibration_frames=0)
        self.assertFalse(vad.process(AudioChunk(b"\0\0" * 320, DEFAULT_INPUT_FORMAT)))
        self.assertTrue(vad.process(AudioChunk((2000).to_bytes(2, "little", signed=True) * 320, DEFAULT_INPUT_FORMAT)))

    def test_segmenter_prefers_sentence_boundary_then_flushes(self) -> None:
        segmenter = TextSegmenter(max_chars=5)
        self.assertEqual(segmenter.push("你好，世界。继续"), ["你好，世界。"])
        self.assertEqual(segmenter.flush(), "继续")

    def test_segmenter_emits_a_short_first_chunk_before_later_chunks(self) -> None:
        segmenter = TextSegmenter(max_chars=8, first_chunk_chars=4)
        self.assertEqual(segmenter.push("第一段回答"), ["第一段回"])
        self.assertEqual(segmenter.push("还会继续生成。"), ["答还会继续生成。"])

    def test_silero_fallback_debounces_confirmed_speech(self) -> None:
        vad = SileroVAD(start_ms=40, stop_ms=40)
        loud = AudioChunk((2000).to_bytes(2, "little", signed=True) * 320, DEFAULT_INPUT_FORMAT)
        quiet = AudioChunk(b"\0\0" * 320, DEFAULT_INPUT_FORMAT)
        self.assertFalse(vad.process(loud))
        self.assertTrue(vad.process(loud))
        self.assertEqual(vad.state, VADState.SPEAKING)
        self.assertFalse(vad.process(quiet))
        self.assertFalse(vad.process(quiet))
        self.assertEqual(vad.state, VADState.QUIET)

    def test_passthrough_filter_preserves_chunk(self) -> None:
        async def check() -> None:
            filter_ = PassthroughAudioFilter()
            await filter_.start(DEFAULT_INPUT_FORMAT)
            chunk = AudioChunk(b"\0\0" * 320, DEFAULT_INPUT_FORMAT, 1.0)
            self.assertEqual(tuple(await filter_.process(chunk)), (chunk,))
            self.assertEqual(tuple(await filter_.flush()), ())
        import asyncio
        asyncio.run(check())

    @unittest.skipUnless(find_spec("pyrnnoise"), "speech extra is not installed")
    def test_rnnoise_processes_a_20ms_pcm_frame(self) -> None:
        async def check() -> None:
            filter_ = RNNoiseFilter(required=True)
            await filter_.start(DEFAULT_INPUT_FORMAT)
            output = tuple(await filter_.process(AudioChunk(b"\0\0" * 320, DEFAULT_INPUT_FORMAT)))
            self.assertEqual(len(output), 1)
            self.assertEqual(len(output[0].data), 640)
            await filter_.close()
        import asyncio
        asyncio.run(check())

    def test_turn_detector_forces_idle_completion(self) -> None:
        detector = TurnDetector(silence_ms=1000, max_duration_ms=1000, idle_timeout_ms=0)
        self.assertEqual(detector.update(True), (True, False))
        self.assertTrue(detector.idle())

class _FakeTransport:
    def __init__(self, audio: Sequence[AudioChunk]) -> None:
        self.audio = audio
        self.events = []
        self.closed = False

    async def receive_audio(self) -> AsyncIterator[AudioChunk]:
        for chunk in self.audio:
            yield chunk

    async def send_audio(self, audio: AudioChunk) -> None:
        return None

    async def send_event(self, event) -> None:
        self.events.append(event)

    async def clear_output(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _DisconnectingTransport(_FakeTransport):
    async def clear_output(self) -> None:
        raise RuntimeError("WebSocket is disconnected")


class _FakeASRSession:
    persistent = False
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.committed = False

    async def start(self) -> None:
        return None

    async def write(self, audio: AudioChunk) -> None:
        self.written.append(audio.data)

    async def commit(self) -> None:
        self.committed = True

    async def events(self):
        if False:
            yield None

    async def close(self) -> None:
        return None


class _FakeASR:
    def __init__(self) -> None:
        self.sessions: list[_FakeASRSession] = []

    def create_session(self) -> _FakeASRSession:
        session = _FakeASRSession()
        self.sessions.append(session)
        return session


class _FakeTTS:
    async def synthesize(self, text: str):
        if False:
            yield None


class _ScriptedVAD:
    state = VADState.QUIET
    confidence = level = 0.0
    def __init__(self, answers: Sequence[bool]) -> None:
        self.answers = iter(answers)

    def process(self, audio: AudioChunk) -> bool:
        value = next(self.answers)
        self.state = VADState.SPEAKING if value else VADState.QUIET
        return value

    def reset(self) -> None:
        return None


class SpeechSessionInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_barge_in_uses_a_short_sustained_confirmation_window(self) -> None:
        vad = _ScriptedVAD([True] * 20)
        vad.confidence, vad.level = 0.7, 0.01
        session = SpeechSession(
            agent_session=object(), transport=_FakeTransport(()), asr=_FakeASR(), tts=_FakeTTS(),
            vad=vad, turn_detector=TurnDetector(), barge_in_ms=400,
            barge_in_confidence=0.6, barge_in_min_volume=0.004,
        )
        session._agent_run = object()  # Simulate an assistant response being spoken.
        audio = AudioChunk(b"\0" * 640, DEFAULT_INPUT_FORMAT)  # 20 ms PCM16.
        for _ in range(19):
            self.assertFalse(session._confirm_barge_in(audio, True))
        self.assertTrue(session._confirm_barge_in(audio, True))

    async def test_only_filtered_preroll_reaches_asr_after_confirmed_start(self) -> None:
        class Filter(PassthroughAudioFilter):
            async def process(self, audio: AudioChunk):
                return (AudioChunk(b"clean-" + audio.data, audio.format),)

        transport = _FakeTransport([
            AudioChunk(b"ambient", DEFAULT_INPUT_FORMAT),
            AudioChunk(b"speech", DEFAULT_INPUT_FORMAT),
            AudioChunk(b"quiet", DEFAULT_INPUT_FORMAT),
        ])
        asr = _FakeASR()
        session = SpeechSession(
            agent_session=object(), transport=transport, asr=asr, tts=_FakeTTS(),
            audio_filter=Filter(), vad=_ScriptedVAD([False, True, False]),
            turn_detector=TurnDetector(silence_ms=0, max_duration_ms=1000, idle_timeout_ms=1000),
        )
        await session.run()
        self.assertEqual(asr.sessions[0].written, [b"clean-ambient", b"clean-speech", b"clean-quiet"])
        self.assertTrue(asr.sessions[0].committed)
        self.assertTrue(transport.closed)

    async def test_noise_transcripts_do_not_start_agent(self) -> None:
        self.assertFalse(SpeechSession._is_meaningful_transcript("…"))
        self.assertFalse(SpeechSession._is_meaningful_transcript("啊"))
        self.assertTrue(SpeechSession._is_meaningful_transcript("你好"))
        self.assertTrue(SpeechSession._is_meaningful_transcript("hello"))

    async def test_close_releases_resources_after_transport_disconnect(self) -> None:
        transport = _DisconnectingTransport(())
        session = SpeechSession(
            agent_session=object(), transport=transport, asr=_FakeASR(), tts=_FakeTTS(),
            vad=_ScriptedVAD(()), turn_detector=TurnDetector(),
        )
        await session.close()
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
