"""Fixed real-time speech pipeline orchestrator."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from time import monotonic

from roboagent.runtime import MessageDeltaEvent
from roboagent.agent.session import SessionBusyError

from .asr.base import ASR, ASRSession
from .audio.buffer import AudioBuffer
from .audio.filter import AudioFilter, PassthroughAudioFilter
from .audio.vad import VAD
from .event import (
    AudioCompletedEvent, AudioStartedEvent, InterruptedEvent, ResponseCompletedEvent,
    ResponseStartedEvent, ResponseTextEvent, SpeechErrorEvent, SpeechEvent,
    SpeechDiagnosticEvent, SpeechStartedEvent, SpeechStoppedEvent, TranscriptFinalEvent, TranscriptPartialEvent,
)
from .text.segmenter import TextSegmenter
from .transport.base import SpeechTransport
from .tts.base import TTS
from .turn.detector import TurnDetector
from .types import AudioChunk, DEFAULT_INPUT_FORMAT

logger = logging.getLogger(__name__)
_STOP = object()


class SpeechSession:
    """Bridge streaming audio services to one existing text ``AgentSession``."""
    def __init__(self, *, agent_session, transport: SpeechTransport, asr: ASR, tts: TTS,
                 vad: VAD, turn_detector: TurnDetector, segmenter: TextSegmenter | None = None,
                 audio_filter: AudioFilter | None = None, queue_size: int = 25,
                 barge_in_ms: int = 400, barge_in_confidence: float = 0.60,
                 barge_in_min_volume: float = 0.004,
                 diagnostics: bool = False) -> None:
        self.agent_session = agent_session
        self.transport, self.asr, self.tts = transport, asr, tts
        self.vad, self.turn_detector = vad, turn_detector
        self.audio_filter = audio_filter or PassthroughAudioFilter()
        self.diagnostics = diagnostics
        self.segmenter = segmenter or TextSegmenter()
        self._tts_queue: asyncio.Queue[str | object] = asyncio.Queue(queue_size)
        self._agent_run = None
        self._asr_session: ASRSession | None = None
        self._closed = False
        self._tts_task: asyncio.Task[None] | None = None
        self._active_tts_task: asyncio.Task[None] | None = None
        self._agent_task: asyncio.Task[None] | None = None
        self._response_generation = 0
        self._input_queue: asyncio.Queue[AudioChunk | object] = asyncio.Queue(maxsize=queue_size)
        self._input_task: asyncio.Task[None] | None = None
        self._audio_task: asyncio.Task[None] | None = None
        self._pre_roll = AudioBuffer(max_bytes=9_600)  # 300 ms of 16 kHz mono PCM16
        self._turn_active = False
        self._dropped_frames = 0
        self._last_diagnostics_at = 0.0
        self._filter_latency_ms = 0.0
        self._ignored_asr_sessions: set[int] = set()
        self._barge_in_ms = barge_in_ms
        self._barge_in_confidence = barge_in_confidence
        self._barge_in_min_volume = barge_in_min_volume
        self._barge_in_seen_ms = 0.0

    async def run(self) -> None:
        self._tts_task = asyncio.create_task(self._tts_worker())
        try:
            await self.audio_filter.start(DEFAULT_INPUT_FORMAT)
            starter = getattr(self.vad, "start", None)
            if starter is not None:
                starter()
            self._input_task = asyncio.create_task(self._receive_audio())
            self._audio_task = asyncio.create_task(self._process_audio())
            await self._input_task
            await self._input_queue.put(_STOP)
            await self._audio_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("speech session failed")
            await self._emit(SpeechErrorEvent(error=str(exc)))
        finally:
            await self.close()

    async def _receive_audio(self) -> None:
        async for audio in self.transport.receive_audio():
            if self._closed:
                return
            if self._input_queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._input_queue.get_nowait()
                    self._dropped_frames += 1
            self._input_queue.put_nowait(audio)

    async def _process_audio(self) -> None:
        while not self._closed:
            try:
                item = await asyncio.wait_for(self._input_queue.get(), timeout=self.turn_detector.idle_timeout_seconds)
            except TimeoutError:
                if self.turn_detector.idle():
                    await self._complete_turn()
                continue
            if item is _STOP:
                for audio in await self.audio_filter.flush():
                    await self._process_filtered(audio)
                if self._turn_active:
                    await self._complete_turn()
                return
            filter_started = monotonic()
            filtered = await self.audio_filter.process(item)
            self._filter_latency_ms = (monotonic() - filter_started) * 1000
            for audio in filtered:
                await self._process_filtered(audio)

    async def _process_filtered(self, audio: AudioChunk) -> None:
        self._pre_roll.append(audio)
        confidence_setter = getattr(self.vad, "set_external_confidence", None)
        if confidence_setter is not None:
            confidence_setter(getattr(self.audio_filter, "speech_probability", None))
        speaking = self.vad.process(audio)
        speaking = self._confirm_barge_in(audio, speaking)
        started, completed = self.turn_detector.update(speaking)
        if started:
            if self._output_active():
                await self.interrupt("barge_in")
            self._turn_active = True
            await self._emit(SpeechStartedEvent())
            await self._start_asr()
            # ASR sees only the filtered pre-roll and subsequent filtered PCM.
            if self._asr_session is not None:
                for pre_roll in self._pre_roll.chunks():
                    await self._asr_session.write(pre_roll)
            self._pre_roll.clear()
        elif self._turn_active and self._asr_session is not None:
            await self._asr_session.write(audio)
        if completed:
            await self._complete_turn()
        await self._emit_diagnostics()

    def _confirm_barge_in(self, audio: AudioChunk, speaking: bool) -> bool:
        """Require sustained, high-confidence speech while output is active.

        Browser echo cancellation is helpful but cannot reliably distinguish a
        nearby speaker from a user.  Do not let a short echoed syllable start a
        new turn and cancel the response that produced it.
        """
        if not self._output_active():
            self._barge_in_seen_ms = 0.0
            return speaking
        duration_ms = len(audio.data) * 1000 / (
            audio.format.sample_rate * audio.format.channels * audio.format.sample_width
        )
        candidate = (
            speaking
            and float(getattr(self.vad, "confidence", 0.0)) >= self._barge_in_confidence
            and float(getattr(self.vad, "level", 0.0)) >= self._barge_in_min_volume
        )
        self._barge_in_seen_ms = self._barge_in_seen_ms + duration_ms if candidate else 0.0
        return candidate and self._barge_in_seen_ms >= self._barge_in_ms

    async def _complete_turn(self) -> None:
        if not self._turn_active:
            return
        self._turn_active = False
        self._pre_roll.clear()
        await self._emit(SpeechStoppedEvent())
        if self._asr_session is not None:
            if not self.turn_detector.last_turn_valid:
                self._ignored_asr_sessions.add(id(self._asr_session))
            logger.info("speech turn completed; finalizing ASR request")
            await self._finish_asr_turn()

    async def _emit_diagnostics(self) -> None:
        if not self.diagnostics or monotonic() - self._last_diagnostics_at < 0.5:
            return
        self._last_diagnostics_at = monotonic()
        await self._emit(SpeechDiagnosticEvent(
            level=float(getattr(self.vad, "level", 0.0)),
            vad_state=str(getattr(self.vad, "state", "unknown")),
            confidence=float(getattr(self.vad, "confidence", 0.0)),
            filter_latency_ms=self._filter_latency_ms,
            dropped_frames=self._dropped_frames,
        ))

    async def _start_asr(self) -> None:
        if self._asr_session is not None:
            return
        session = self.asr.create_session()
        await session.start()
        self._asr_session = session
        asyncio.create_task(self._consume_transcripts(session))

    async def _finish_asr_turn(self) -> None:
        """Stop one ASR turn without leaving new PCM pointed at it."""
        session, self._asr_session = self._asr_session, None
        if session is not None:
            await session.commit()

    def _output_active(self) -> bool:
        return (
            self._agent_run is not None
            or (self._agent_task is not None and not self._agent_task.done())
            or (self._active_tts_task is not None and not self._active_tts_task.done())
            or not self._tts_queue.empty()
        )

    async def _consume_transcripts(self, session: ASRSession) -> None:
        try:
            async for transcript in session.events():
                if self._closed:
                    return
                if transcript.final:
                    if id(session) in self._ignored_asr_sessions:
                        self._ignored_asr_sessions.discard(id(session))
                        return
                    await self._emit(TranscriptFinalEvent(text=transcript.text))
                    if self._is_meaningful_transcript(transcript.text):
                        await self._start_agent(transcript.text)
                    if not getattr(session, "persistent", False):
                        await session.close()
                        if self._asr_session is session:
                            self._asr_session = None
                else:
                    await self._emit(TranscriptPartialEvent(text=transcript.text))
        except Exception as exc:
            await self._emit(SpeechErrorEvent(error=str(exc)))

    @staticmethod
    def _is_meaningful_transcript(text: str) -> bool:
        compact = re.sub(r"[\W_]", "", text, flags=re.UNICODE)
        if not compact:
            return False
        chinese = sum("\u4e00" <= char <= "\u9fff" for char in compact)
        latin = sum(char.isascii() and char.isalpha() for char in compact)
        return chinese >= 2 or latin >= 3 or len(compact) >= 3

    async def _start_agent(self, text: str) -> None:
        if not text.strip():
            return
        self._response_generation += 1
        generation = self._response_generation
        if self._agent_task is not None and not self._agent_task.done():
            await self.interrupt("new_turn")
        self._agent_task = asyncio.create_task(self._consume_agent(text, generation))

    async def _consume_agent(self, text: str, generation: int) -> None:
        run = None
        try:
            # Text chat and voice intentionally share one AgentSession.  Wait
            # for a text-originated run to finish; newer voice transcripts
            # supersede this pending one instead of creating concurrent runs.
            while generation == self._response_generation:
                try:
                    run = self.agent_session.start(text)
                    break
                except SessionBusyError:
                    await asyncio.sleep(0.05)
            if run is None or generation != self._response_generation:
                return
            self._agent_run = run
            await self._emit(ResponseStartedEvent())
            async for event in run.events():
                if generation != self._response_generation:
                    run.cancel("superseded")
                    return
                if isinstance(event, MessageDeltaEvent) and event.kind == "text":
                    await self._emit(ResponseTextEvent(delta=event.delta))
                    for segment in self.segmenter.push(event.delta):
                        await self._tts_queue.put(segment)
            tail = self.segmenter.flush()
            if tail:
                await self._tts_queue.put(tail)
            await run.result()
            await self._emit(ResponseCompletedEvent())
        except asyncio.CancelledError:
            if run is not None:
                run.cancel("speech_interrupt")
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(run.result())
            raise
        except Exception as exc:
            await self._emit(SpeechErrorEvent(error=str(exc)))
        finally:
            if run is not None and self._agent_run is run:
                self._agent_run = None

    async def _tts_worker(self) -> None:
        while True:
            text = await self._tts_queue.get()
            if text is _STOP:
                return
            self._active_tts_task = asyncio.create_task(self._synthesize_to_transport(str(text)))
            try:
                await self._active_tts_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A dropped provider socket affects this fragment only. The
                # next fragment reconnects instead of permanently silencing
                # the rest of a long assistant response.
                logger.exception("TTS fragment failed; keeping the worker alive")
                await self._emit(SpeechErrorEvent(error=str(exc)))
            finally:
                self._active_tts_task = None

    async def _synthesize_to_transport(self, text: str) -> None:
        started = False
        try:
            async for audio in self.tts.synthesize(text):
                # Do not report playback for keepalive/empty provider deltas; the
                # browser cannot schedule an empty PCM buffer and would otherwise
                # remain stuck on the playing status.
                if not audio.data:
                    continue
                if not started:
                    started = True
                    logger.info("TTS returned the first PCM frame for the current response.")
                    await self._emit(AudioStartedEvent())
                await self.transport.send_audio(audio)
            if started:
                await self._emit(AudioCompletedEvent())
            else:
                logger.warning("TTS response completed without PCM audio.")
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

    async def interrupt(self, reason: str = "barge_in") -> None:
        if self._agent_run is not None:
            self._agent_run.cancel(reason)
        if self._agent_task is not None:
            self._agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._agent_task
        self._agent_task, self._agent_run = None, None
        if self._active_tts_task is not None:
            self._active_tts_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._active_tts_task
            self._active_tts_task = None
        canceller = getattr(self.tts, "cancel", None)
        if canceller is not None:
            with contextlib.suppress(Exception):
                await canceller()
        self.segmenter.reset()
        while not self._tts_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._tts_queue.get_nowait()
        await self.transport.clear_output()
        await self._emit(InterruptedEvent(reason=reason))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # A browser may have closed its WebSocket before this coroutine gets
        # scheduled.  Do not let a best-effort playback-clear notification
        # prevent ASR, TTS, DSP and task cleanup below.
        with contextlib.suppress(Exception):
            await self.interrupt("closed")
        if self._asr_session is not None:
            with contextlib.suppress(Exception):
                await self._asr_session.close()
            self._asr_session = None
        for task in (self._input_task, self._audio_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        with contextlib.suppress(Exception):
            await self.audio_filter.close()
        closer = getattr(self.tts, "close", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
        if self._tts_task is not None:
            self._tts_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tts_task
        with contextlib.suppress(Exception):
            await self.transport.close()

    async def _emit(self, event: SpeechEvent) -> None:
        if not self._closed or isinstance(event, (SpeechErrorEvent, InterruptedEvent)):
            await self.transport.send_event(event)
