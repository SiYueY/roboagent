"""ASR -> canonical Agent/Session -> TTS survives the breaking migration."""

import asyncio

from roboagent import Agent
from roboagent.interaction.speech import SpeechSession
from roboagent.interaction.speech.event import AudioCompletedEvent, SpeechErrorEvent
from roboagent.interaction.speech.turn.detector import TurnDetector
from roboagent.interaction.speech.types import (
    AudioChunk,
    DEFAULT_INPUT_FORMAT,
    DEFAULT_OUTPUT_FORMAT,
    Transcript,
)
from roboagent.message import AssistantMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
)
from tests.speech.test_primitives import _ScriptedVAD


def test_asr_agent_tts_canonical_session_roundtrip():
    async def check():
        completed = asyncio.Event()

        class Transport:
            def __init__(self):
                self.audio = []
                self.events = []
                self.closed = False

            async def receive_audio(self):
                yield AudioChunk(b"x" * 640, DEFAULT_INPUT_FORMAT)
                yield AudioChunk(b"\0" * 640, DEFAULT_INPUT_FORMAT)
                await completed.wait()

            async def send_audio(self, chunk):
                self.audio.append(chunk)

            async def send_event(self, event):
                self.events.append(event)
                if isinstance(event, AudioCompletedEvent):
                    completed.set()

            async def clear_output(self):
                self.cleared = True

            async def close(self):
                self.closed = True

        class ASRSession:
            persistent = False

            def __init__(self):
                self.committed = asyncio.Event()
                self.written = []

            async def start(self):
                pass

            async def write(self, chunk):
                self.written.append(chunk.data)

            async def commit(self):
                self.committed.set()

            async def events(self):
                await self.committed.wait()
                yield Transcript("hello robot", True)

            async def close(self):
                pass

        class ASR:
            def create_session(self):
                return recognized

        class TTS:
            def __init__(self):
                self.texts = []

            async def synthesize(self, text):
                self.texts.append(text)
                yield AudioChunk(b"\0" * 960, DEFAULT_OUTPUT_FORMAT)

        class Model:
            capabilities = ModelCapabilities()

            async def stream(self, context, settings=None):
                yield ResponseStarted("speech", 0)
                yield TextDelta(1, "Hello human.")
                yield ResponseCompleted(
                    2,
                    ModelResponse(AssistantMessage("Hello human."), FinishReason.STOP),
                )

        recognized = ASRSession()
        transport, tts = Transport(), TTS()
        canonical = Agent(Model()).new_session()
        speech = SpeechSession(
            session=canonical,
            transport=transport,
            asr=ASR(),
            tts=tts,
            vad=_ScriptedVAD([True, False]),
            turn_detector=TurnDetector(silence_ms=0, min_speech_ms=0),
            queue_size=4,
        )
        await asyncio.wait_for(speech.run(), timeout=3)
        assert recognized.written and transport.audio and tts.texts == ["Hello human."]
        assert transport.closed
        assert not any(
            isinstance(event, SpeechErrorEvent) for event in transport.events
        )
        assert [message.content[0].text for message in canonical.messages] == [
            "hello robot",
            "Hello human.",
        ]
        await canonical.close()

    asyncio.run(check())
