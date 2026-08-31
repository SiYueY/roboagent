"""FastAPI WebSocket adapter for the framework-independent speech runtime."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from time import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from roboagent.speech.asr.dashscope import DashScopeASR
from roboagent.speech.audio import EnergyVAD, PassthroughAudioFilter, RNNoiseFilter, SileroVAD
from roboagent.speech.errors import SpeechConfigurationError
from roboagent.speech.config import SpeechConfig
from roboagent.speech.session import SpeechSession
from roboagent.speech.text.segmenter import TextSegmenter
from roboagent.speech.transport.base import SpeechTransport
from roboagent.speech.tts.dashscope import DashScopeTTS
from roboagent.speech.turn.detector import TurnDetector
from roboagent.speech.types import AudioChunk, DEFAULT_INPUT_FORMAT

logger = logging.getLogger(__name__)


def _make_audio_filter(config: SpeechConfig):
    options = config.audio_filter
    if options.provider == "passthrough":
        return PassthroughAudioFilter()
    if options.provider == "rnnoise":
        return RNNoiseFilter(required=options.required, quality=options.resampler_quality)
    raise SpeechConfigurationError(
        "Krisp requires the separately installed vendor adapter and model; choose rnnoise or passthrough."
    )


def _make_vad(config: SpeechConfig):
    options = config.vad
    if options.provider == "energy":
        return EnergyVAD(
            options.threshold,
            noise_multiplier=options.noise_multiplier,
            calibration_frames=max(0, options.calibration_ms // 20),
        )
    return SileroVAD(
        confidence=options.confidence,
        min_volume=options.min_volume,
        start_ms=options.start_ms,
        stop_ms=options.stop_ms,
        model_path=options.model_path,
        required=options.required,
    )


class ConversationRegistry:
    """In-memory page-private capabilities for chat speech connections."""
    def __init__(self) -> None:
        self._items: dict[str, Any] = {}

    def register(self, conversation: Any) -> str:
        # Conversation IDs are UUID4 values created server-side and are already
        # unguessable page-private capabilities; using the same value lets the
        # browser follow Gradio's active radio selection without stale tokens.
        token = conversation.id
        self._items[token] = conversation
        return token

    def get(self, token: str | None) -> Any | None:
        return self._items.get(token or "")

    def discard(self, token: str) -> None:
        self._items.pop(token, None)


class WebSocketSpeechTransport(SpeechTransport):
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._closed = False
        self._received_audio = False

    async def receive_audio(self):
        while not self._closed:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                if not self._received_audio:
                    logger.info("Speech WebSocket is receiving microphone PCM audio.")
                    self._received_audio = True
                yield AudioChunk(message["bytes"], DEFAULT_INPUT_FORMAT)
                continue
            text = message.get("text")
            if text:
                payload = json.loads(text)
                if payload.get("type") in {"session.cancel", "session.close"}:
                    return

    async def send_audio(self, audio: AudioChunk) -> None:
        await self.websocket.send_bytes(audio.data)

    async def send_event(self, event) -> None:
        payload = asdict(event) if is_dataclass(event) else dict(event)
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def clear_output(self) -> None:
        await self.websocket.send_text('{"type":"playback.clear"}')

    async def close(self) -> None:
        self._closed = True


def install_speech_route(app: FastAPI, registry: ConversationRegistry, config: SpeechConfig) -> None:
    @app.websocket("/speech")
    async def speech(websocket: WebSocket) -> None:
        token = websocket.query_params.get("token")
        conversation = registry.get(token)
        if conversation is None:
            await websocket.close(code=1008, reason="Invalid speech session token")
            return
        await websocket.accept()
        logger.info("Speech WebSocket connected for the active chat conversation.")
        transport = WebSocketSpeechTransport(websocket)
        try:
            session = SpeechSession(
                agent_session=conversation.session,
                transport=transport,
                asr=DashScopeASR(config.asr),
                tts=DashScopeTTS(config.tts),
                audio_filter=_make_audio_filter(config),
                vad=_make_vad(config),
                turn_detector=TurnDetector(
                    config.turn.silence_ms,
                    config.turn.max_duration_ms,
                    config.turn.idle_timeout_ms,
                    config.turn.min_speech_ms,
                ),
                segmenter=TextSegmenter(
                    config.tts.chunk_chars,
                    first_chunk_chars=config.tts.first_chunk_chars,
                ),
                barge_in_ms=config.turn.barge_in_ms,
                barge_in_confidence=config.turn.barge_in_confidence,
                barge_in_min_volume=config.turn.barge_in_min_volume,
                diagnostics=config.diagnostics,
            )
        except SpeechConfigurationError as exc:
            await transport.send_event({"type": "error", "error": str(exc)})
            await websocket.close(code=1011, reason="Invalid speech configuration")
            return
        async def record_events() -> None:
            original = transport.send_event
            async def record(event):
                event_type = event.get("type") if isinstance(event, dict) else event.type
                if event_type == "transcript.final" and session._is_meaningful_transcript(event.text):
                    conversation.history += [{"role": "user", "content": event.text}, {"role": "assistant", "content": "正在思考…"}]
                    if len(conversation.history) == 2:
                        conversation.title = event.text[:24] or "新对话"
                    conversation.updated_at = time()
                elif event_type == "response.delta" and conversation.history:
                    conversation.history[-1] = {"role": "assistant", "content": conversation.history[-1]["content"].replace("正在思考…", "") + event.delta}
                    conversation.updated_at = time()
                await original(event)
            transport.send_event = record
        await record_events()
        await transport.send_event({"type": "session.ready", "timestamp": time()})
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()
