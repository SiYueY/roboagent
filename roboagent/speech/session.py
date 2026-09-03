"""Fixed real-time speech pipeline orchestrator."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import replace
from time import monotonic

from roboagent.runtime import AgentEvent
from roboagent.agent.session import SessionBusyError

from .asr.base import ASR, ASRSession
from .audio.buffer import AudioBuffer
from .audio.frame import AudioFrameAssembler
from .audio.passthrough import PassthroughAudioProcessor
from .audio.pcm import convert_pcm16
from .audio.processor import AudioProcessor
from .audio.vad import VAD
from .event import (
    AudioCompletedEvent, AudioStartedEvent, FalseInterruptionEvent, InterruptedEvent, PlaybackBeginEvent, ResponseCompletedEvent,
    ResponseStartedEvent, ResponseTextEvent, SpeechErrorEvent, SpeechEvent,
    SpeechDiagnosticEvent, SpeechMetricsEvent, SpeechStartedEvent, SpeechStoppedEvent, TranscriptFinalEvent, TranscriptPartialEvent,
)
from .metrics import SpeechMetrics
from .text.segmenter import TextSegmenter
from .transport.base import SpeechTransport
from .tts.base import TTS
from .turn.detector import TurnDetector
from .turn.interruption import InterruptionDetector
from .types import AudioChunk, AudioFormat, DEFAULT_INPUT_FORMAT, DEFAULT_OUTPUT_FORMAT

logger = logging.getLogger(__name__)
_STOP = object()


class SpeechSession:
    """Bridge streaming audio services to one existing text ``AgentSession``."""
    def __init__(self, *, agent_session, transport: SpeechTransport, asr: ASR, tts: TTS,
                 vad: VAD, turn_detector: TurnDetector, segmenter: TextSegmenter | None = None,
                 audio_processor: AudioProcessor | None = None, interruption_detector: InterruptionDetector | None = None,
                 capture_format: AudioFormat = DEFAULT_INPUT_FORMAT, render_format: AudioFormat = DEFAULT_OUTPUT_FORMAT,
                 # Two seconds absorbs WebSocket startup bursts.  ASR writes
                 # run on their own worker, so this is not normal playout
                 # latency; it only prevents losing an utterance on a short
                 # provider/network stall.
                 queue_size: int = 100,
                 diagnostics: bool = False) -> None:
        self.agent_session = agent_session
        self.transport, self.asr, self.tts = transport, asr, tts
        self.vad, self.turn_detector = vad, turn_detector
        self.audio_processor = audio_processor or PassthroughAudioProcessor()
        self.interruption_detector = interruption_detector or InterruptionDetector()
        self.capture_format, self.render_format = capture_format, render_format
        self.diagnostics = diagnostics
        self.segmenter = segmenter or TextSegmenter()
        self._tts_queue: asyncio.Queue[tuple[str, int] | object] = asyncio.Queue(queue_size)
        self._agent_run = None
        self._asr_session: ASRSession | None = None
        self._asr_queue: asyncio.Queue[AudioChunk | object] | None = None
        self._asr_writer_task: asyncio.Task[None] | None = None
        self._closed = False
        self._tts_task: asyncio.Task[None] | None = None
        self._active_tts_task: asyncio.Task[None] | None = None
        self._agent_task: asyncio.Task[None] | None = None
        self._response_generation = 0
        self._input_queue: asyncio.Queue[AudioChunk | object] = asyncio.Queue(maxsize=queue_size)
        self._input_task: asyncio.Task[None] | None = None
        self._audio_task: asyncio.Task[None] | None = None
        self._pre_roll = AudioBuffer(max_bytes=9_600)  # 300 ms of 16 kHz mono PCM16
        self._frame_assembler = AudioFrameAssembler(DEFAULT_INPUT_FORMAT)
        self._turn_active = False
        self._dropped_frames = 0
        self._last_diagnostics_at = 0.0
        self._audio_process_ms = 0.0
        self._ignored_asr_sessions: set[int] = set()
        self._metrics = SpeechMetrics()
        self._metrics_emitted = False
        self._metrics_by_response: dict[int, SpeechMetrics] = {}
        self._metrics_emitted_response: set[int] = set()
        self._response_turn_ids: dict[int, int] = {}
        self._response_audio_process_starts: dict[int, float] = {}
        self._response_dropped_starts: dict[int, int] = {}
        self._response_completed = False
        self._audio_process_started = 0.0
        self._interruption_candidate_at: float | None = None
        self._interruption_candidate_ready = False
        self._turn_id = 0
        self._response_id: int | None = None

    async def run(self) -> None:
        self._tts_task = asyncio.create_task(self._tts_worker())
        try:
            await self.audio_processor.start(self.capture_format, self.render_format)
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
                for audio in await self.audio_processor.flush_capture():
                    await self._process_capture(audio)
                for audio in self._frame_assembler.flush():
                    await self._process_canonical_frame(audio)
                if self._turn_active:
                    await self._complete_turn()
                return
            started = monotonic()
            processed = await self.audio_processor.process_capture(item)
            self._audio_process_ms += (monotonic() - started) * 1000
            for audio in processed:
                await self._process_capture(audio)

    async def _process_capture(self, audio: AudioChunk) -> None:
        audio = convert_pcm16(audio, DEFAULT_INPUT_FORMAT)
        for frame in self._frame_assembler.push(audio):
            await self._process_canonical_frame(frame)

    async def _process_canonical_frame(self, audio: AudioChunk) -> None:
        self._metrics.input_frames += 1
        self._pre_roll.append(audio)
        confidence_setter = getattr(self.vad, "set_external_confidence", None)
        if confidence_setter is not None:
            confidence_setter(getattr(self.audio_processor, "speech_probability", None))
        speaking = self.vad.process(audio)
        output_active = self._output_active()
        decision = self.interruption_detector.update(
            speaking=speaking, confidence=float(getattr(self.vad, "confidence", 0.0)),
            level=float(getattr(self.vad, "level", 0.0)), output_active=output_active,
            duration_ms=len(audio.data) * 1000 / (audio.format.sample_rate * audio.format.channels * audio.format.sample_width),
        )
        if decision.false_interruption:
            self._interruption_candidate_at = None
            await self._emit(FalseInterruptionEvent())
        if decision.candidate and self._interruption_candidate_at is None:
            self._interruption_candidate_at = monotonic()
        # Do not cancel merely because VAD heard audio while we are playing.
        # We start/continue ASR for that candidate and require meaningful text
        # in _consume_transcripts before committing the interruption.
        self._interruption_candidate_ready = decision.confirmed
        started, completed = self.turn_detector.update(speaking)
        if started:
            self._turn_active = True
            self._turn_id += 1
            self._metrics = SpeechMetrics(input_frames=1)
            self._audio_process_started = self._audio_process_ms
            reset_playback_metrics = getattr(self.transport, "reset_playback_metrics", None)
            if reset_playback_metrics is not None:
                reset_playback_metrics()
            self._metrics.mark("speech_started_at")
            self._metrics_emitted = False
            await self._emit(SpeechStartedEvent())
            await self._start_asr()
            # ASR sees only the filtered pre-roll and subsequent filtered PCM.
            if self._asr_session is not None:
                for pre_roll in self._pre_roll.chunks():
                    self._enqueue_asr(pre_roll)
            self._pre_roll.clear()
        elif self._turn_active and self._asr_session is not None:
            self._enqueue_asr(audio)
        if completed:
            await self._complete_turn()
        await self._emit_diagnostics()

    async def _complete_turn(self) -> None:
        if not self._turn_active:
            return
        self._turn_active = False
        self._pre_roll.clear()
        self._metrics.mark("speech_stopped_at")
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
            filter_latency_ms=self._audio_process_ms,
            dropped_frames=self._dropped_frames,
        ))

    async def _start_asr(self) -> None:
        if self._asr_session is not None:
            return
        session = self.asr.create_session()
        await session.start()
        self._asr_session = session
        self._asr_queue = asyncio.Queue(maxsize=100)
        self._asr_writer_task = asyncio.create_task(self._write_asr_audio(session, self._asr_queue))
        asyncio.create_task(self._consume_transcripts(session))

    def _enqueue_asr(self, audio: AudioChunk) -> None:
        """Keep slow provider writes out of the capture/VAD critical path."""
        queue = self._asr_queue
        if queue is None:
            return
        if queue.full():
            # Prefer recent speech when the remote ASR is unavailable for
            # longer than the bounded two-second budget.  This is observable
            # through dropped_frames instead of silently growing latency.
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
                self._dropped_frames += 1
        queue.put_nowait(audio)

    async def _write_asr_audio(self, session: ASRSession,
                               queue: asyncio.Queue[AudioChunk | object]) -> None:
        try:
            while (audio := await queue.get()) is not _STOP:
                await session.write(audio)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ASR audio writer failed")
            await self._emit(SpeechErrorEvent(error=str(exc)))

    async def _finish_asr_turn(self) -> None:
        """Stop one ASR turn without leaving new PCM pointed at it."""
        session, self._asr_session = self._asr_session, None
        if session is not None:
            queue, writer = self._asr_queue, self._asr_writer_task
            self._asr_queue = self._asr_writer_task = None
            if queue is not None:
                if queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                        self._dropped_frames += 1
                queue.put_nowait(_STOP)
            if writer is not None:
                await writer
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
                # A sustained acoustic candidate is intentionally insufficient
                # to barge in.  Confirm it from recognisable speech so
                # loudspeaker leakage does not repeatedly abort responses.
                if (self._interruption_candidate_ready and self._output_active()
                        and self._is_interruption_transcript(transcript.text)):
                    self._metrics.interruption_candidate_at = self._interruption_candidate_at
                    await self.interrupt("barge_in")
                    self._interruption_candidate_ready = False
                    self._interruption_candidate_at = None
                if transcript.final:
                    self._metrics.mark("asr_final_at")
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
                    self._metrics.mark("asr_first_partial_at")
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

    @classmethod
    def _is_interruption_transcript(cls, text: str) -> bool:
        """Allow short natural stop words without treating random noise as text."""
        compact = re.sub(r"[\W_]", "", text, flags=re.UNICODE).lower()
        return compact in {"停", "喂", "嗯"} or cls._is_meaningful_transcript(text)

    async def _start_agent(self, text: str) -> None:
        if not text.strip():
            return
        if self._agent_task is not None and not self._agent_task.done():
            await self.interrupt("new_turn")
        self._response_generation += 1
        self._response_completed = False
        generation = self._response_generation
        self._response_id = generation
        self._metrics_by_response[generation] = self._metrics
        self._response_turn_ids[generation] = self._turn_id
        self._response_audio_process_starts[generation] = self._audio_process_started
        self._response_dropped_starts[generation] = self._dropped_frames
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
                    run.cancel()
                    return
                if isinstance(event, AgentEvent) and event.type == "model_delta" and event.text:
                    self._metrics_by_response.get(generation, self._metrics).mark("agent_first_token_at")
                    await self._emit(ResponseTextEvent(delta=event.text))
                    for segment in self.segmenter.push(event.text):
                        await self._tts_queue.put((segment, generation))
            tail = self.segmenter.flush()
            if tail:
                await self._tts_queue.put((tail, generation))
            await run.result()
            await self._emit(ResponseCompletedEvent())
            self._response_completed = True
            await self._emit_metrics_if_complete(generation)
        except asyncio.CancelledError:
            if run is not None:
                run.cancel()
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
            item = await self._tts_queue.get()
            if item is _STOP:
                return
            text, generation = item
            if generation != self._response_generation:
                continue
            self._active_tts_task = asyncio.create_task(self._synthesize_to_transport(text, generation))
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
                await self._emit_metrics_if_complete(generation)

    async def _synthesize_to_transport(self, text: str, generation: int) -> None:
        emitted_audio = False
        try:
            async for audio in self.tts.synthesize(text):
                if generation != self._response_generation:
                    return
                # Do not report playback for keepalive/empty provider deltas; the
                # browser cannot schedule an empty PCM buffer and would otherwise
                # remain stuck on the playing status.
                if not audio.data:
                    continue
                if not emitted_audio:
                    logger.info("TTS returned the first PCM frame for the current response.")
                    await self._emit(PlaybackBeginEvent())
                for rendered in await self.audio_processor.process_render(audio):
                    if generation != self._response_generation:
                        return
                    await self.transport.send_audio(rendered)
                if not emitted_audio:
                    emitted_audio = True
                    self._metrics_by_response.get(generation, self._metrics).mark("tts_first_audio_at")
                    await self._emit(AudioStartedEvent())
            if emitted_audio:
                await self._emit(AudioCompletedEvent())
            else:
                logger.warning("TTS response completed without PCM audio.")
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

    async def interrupt(self, reason: str = "barge_in") -> None:
        # Invalidate queued and late provider output before touching transport
        # state.  Every producer checks this generation before emitting PCM.
        cancelled_response_id = self._response_id
        self._response_generation += 1
        self._response_id = None
        if self._agent_run is not None:
            self._agent_run.cancel()
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
        self.interruption_detector.reset()
        self._interruption_candidate_ready = False
        while not self._tts_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._tts_queue.get_nowait()
        await self.transport.clear_output()
        self._metrics_by_response.get(cancelled_response_id, self._metrics).mark("interruption_at")
        resetter = getattr(self.audio_processor, "reset", None)
        if resetter is not None:
            resetter()
        await self._emit(InterruptedEvent(reason=reason, response_id=cancelled_response_id))

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
        if self._asr_writer_task is not None:
            self._asr_writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._asr_writer_task
            self._asr_writer_task = None
            self._asr_queue = None
        for task in (self._input_task, self._audio_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        with contextlib.suppress(Exception):
            await self.audio_processor.close()
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
            await self.transport.send_event(replace(
                event,
                turn_id=event.turn_id if event.turn_id is not None else self._turn_id or None,
                response_id=event.response_id if event.response_id is not None else self._response_id,
            ))

    async def _emit_metrics_if_complete(self, generation: int) -> None:
        if not self._response_completed or not self._tts_queue.empty() or self._active_tts_task is not None:
            return
        await self._emit_metrics(generation)

    async def _emit_metrics(self, generation: int) -> None:
        if generation in self._metrics_emitted_response:
            return
        self._metrics_emitted_response.add(generation)
        metrics = self._metrics_by_response.pop(generation, self._metrics)
        turn_id = self._response_turn_ids.pop(generation, self._turn_id)
        metrics.audio_process_ms = self._audio_process_ms - self._response_audio_process_starts.pop(
            generation, self._audio_process_started,
        )
        metrics.dropped_frames = self._dropped_frames - self._response_dropped_starts.pop(
            generation, self._dropped_frames,
        )
        queue_latency_getter = getattr(self.transport, "playback_queue_latency_ms", None)
        if queue_latency_getter is not None:
            metrics.playback_queue_latency_ms = float(queue_latency_getter())
        playback_started_getter = getattr(self.transport, "playback_started_at", None)
        if playback_started_getter is not None:
            metrics.playback_started_at = playback_started_getter()
        durations = metrics.durations()
        await self._emit(SpeechMetricsEvent(
            input_frames=metrics.input_frames, dropped_frames=metrics.dropped_frames,
            audio_process_ms=metrics.audio_process_ms,
            playback_queue_latency_ms=metrics.playback_queue_latency_ms,
            turn_id=turn_id or None, response_id=generation, **durations,
        ))
