"""FastAPI WebSocket adapter for the framework-independent speech runtime."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from time import monotonic, time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from roboagent.speech.config import SpeechConfig
from roboagent.speech.factory import create_speech_session
from roboagent.speech.errors import SpeechConfigurationError
from roboagent.speech.transport.base import SpeechTransport
from roboagent.speech.types import AudioChunk, DEFAULT_INPUT_FORMAT

logger = logging.getLogger(__name__)


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
        self._first_playback_started_at: float | None = None
        self._active_response_id: int | None = None

    def playback_started_at(self) -> float | None:
        return self._first_playback_started_at

    def reset_playback_metrics(self) -> None:
        self._first_playback_started_at = None

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
                if (
                    payload.get("type") == "playback.started"
                    and self._first_playback_started_at is None
                ):
                    self._first_playback_started_at = monotonic()
                if payload.get("type") in {"session.cancel", "session.close"}:
                    return

    async def send_audio(self, audio: AudioChunk) -> None:
        await self.websocket.send_bytes(audio.data)

    async def send_event(self, event) -> None:
        payload = asdict(event) if is_dataclass(event) else dict(event)
        if payload.get("type") == "playback.begin":
            self._active_response_id = payload.get("response_id")
        if payload.get("type") == "speech.metrics":
            # Safe structured telemetry: event payload intentionally has no
            # transcript, PCM data, or credentials.
            logger.info(
                "speech_metrics %s",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def clear_output(self) -> None:
        await self.websocket.send_text(
            json.dumps(
                {
                    "type": "playback.clear",
                    "response_id": self._active_response_id,
                }
            )
        )
        self._active_response_id = None

    async def close(self) -> None:
        self._closed = True


def install_speech_route(
    app: FastAPI, registry: ConversationRegistry, config: SpeechConfig
) -> None:
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
            session = create_speech_session(
                agent_session=conversation.session, transport=transport, config=config
            )
        except SpeechConfigurationError as exc:
            await transport.send_event({"type": "error", "error": str(exc)})
            await websocket.close(code=1011, reason="Invalid speech configuration")
            return

        async def record_events() -> None:
            original = transport.send_event

            async def record(event):
                event_type = (
                    event.get("type") if isinstance(event, dict) else event.type
                )
                if (
                    event_type == "transcript.final"
                    and session._is_meaningful_transcript(event.text)
                ):
                    conversation.history += [
                        {"role": "user", "content": event.text},
                        {"role": "assistant", "content": "正在思考…"},
                    ]
                    if len(conversation.history) == 2:
                        conversation.title = event.text[:24] or "新对话"
                    conversation.updated_at = time()
                elif event_type == "response.delta" and conversation.history:
                    conversation.history[-1] = {
                        "role": "assistant",
                        "content": conversation.history[-1]["content"].replace(
                            "正在思考…", ""
                        )
                        + event.delta,
                    }
                    conversation.updated_at = time()
                await original(event)

            transport.send_event = record

        await record_events()
        await transport.send_event(
            {"type": "session.ready", "timestamp": time(), "mode": config.mode}
        )
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()
