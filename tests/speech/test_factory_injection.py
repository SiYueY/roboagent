"""create_speech_session injection points (host-provided ASR/TTS/VAD/processor).

A host that drives speech through its own media layer must be able to compose a
speech session without the DashScope SDK or the native VAD models.  These tests
pin that contract so the defaults cannot silently swallow the overrides.
"""
from __future__ import annotations

import unittest

from roboagent.speech.config import SpeechConfig
from roboagent.speech.factory import create_speech_session
from roboagent.speech.types import AudioChunk, DEFAULT_INPUT_FORMAT


class _FakeASR:
    def create_session(self):  # pragma: no cover - never started here
        raise AssertionError("not used")


class _FakeTTS:
    def synthesize(self, text: str):  # pragma: no cover - never started here
        raise AssertionError("not used")


class _FakeVAD:
    pass


class _FakeProcessor:
    pass


class _FakeTransport:
    async def send_audio(self, audio: AudioChunk) -> None:  # pragma: no cover
        return None

    async def send_event(self, event: object) -> None:  # pragma: no cover
        return None

    async def clear_output(self) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:  # pragma: no cover
        return None


class SpeechFactoryInjectionTests(unittest.TestCase):
    def test_host_supplied_collaborators_are_used(self) -> None:
        asr, tts, vad, processor = _FakeASR(), _FakeTTS(), _FakeVAD(), _FakeProcessor()
        session = create_speech_session(
            session=object(),
            transport=_FakeTransport(),
            config=SpeechConfig(),
            asr=asr,
            tts=tts,
            vad=vad,
            audio_processor=processor,
        )
        self.assertIs(session.asr, asr)
        self.assertIs(session.tts, tts)
        self.assertIs(session.vad, vad)
        self.assertIs(session.audio_processor, processor)

    def test_partial_injection_keeps_other_defaults(self) -> None:
        asr = _FakeASR()
        session = create_speech_session(
            session=object(),
            transport=_FakeTransport(),
            config=SpeechConfig(),
            asr=asr,
        )
        self.assertIs(session.asr, asr)
        # The untouched collaborators still come from the built-in factories.
        self.assertIsNotNone(session.tts)
        self.assertIsNotNone(session.vad)
        self.assertIsNotNone(session.audio_processor)

    def test_audio_format_defaults_are_unchanged(self) -> None:
        session = create_speech_session(
            session=object(),
            transport=_FakeTransport(),
            config=SpeechConfig(),
            asr=_FakeASR(),
            tts=_FakeTTS(),
            vad=_FakeVAD(),
            audio_processor=_FakeProcessor(),
        )
        self.assertEqual(session.capture_format, DEFAULT_INPUT_FORMAT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
