"""DashScope streaming recognition adapter."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

from .._dashscope import get_field
from ..config import DashScopeASRConfig
from ..errors import ASRError
from ..types import AudioChunk, Transcript


def _require_sdk():
    try:
        import dashscope

        return dashscope
    except ImportError as exc:
        raise RuntimeError(
            "DashScope speech support requires `pip install roboagent[speech]`."
        ) from exc


class DashScopeASR:
    def __init__(self, config: DashScopeASRConfig) -> None:
        self.config = config

    def create_session(self) -> "DashScopeASRSession":
        return DashScopeASRSession(self.config)


class DashScopeASRSession:
    def __init__(self, config: DashScopeASRConfig) -> None:
        self.config = config
        self.persistent = config.persistent
        self._queue: asyncio.Queue[Transcript | Exception | None] = asyncio.Queue()
        self._recognition: Any = None
        self._closed = False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        dashscope = _require_sdk()
        from dashscope.audio.asr import RecognitionCallback, RecognitionResult

        config, queue = self.config, self._queue

        class Callback(RecognitionCallback):
            def on_open(self) -> None:
                pass

            def on_close(self) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, None)

            def on_event(self, result: Any) -> None:
                sentence = (
                    result.get_sentence() if hasattr(result, "get_sentence") else None
                )
                if isinstance(sentence, list):
                    sentence = sentence[-1] if sentence else None
                text = get_field(sentence, "text", "")
                if text:
                    final = RecognitionResult.is_sentence_end(sentence)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, Transcript(text=text, final=final)
                    )

            def on_error(self, message: Any) -> None:
                detail = (
                    get_field(message, "message", None)
                    or get_field(message, "code", None)
                    or repr(message)
                )
                loop.call_soon_threadsafe(queue.put_nowait, ASRError(str(detail)))

            def on_complete(self) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        try:
            if api_key := (config.api_key or os.getenv("DASHSCOPE_API_KEY")):
                dashscope.api_key = api_key
            self._recognition = dashscope.audio.asr.Recognition(
                model=config.model,
                format="pcm",
                sample_rate=16_000,
                callback=Callback(),
                workspace=config.workspace_id,
                language_hints=list(config.language_hints),
                max_sentence_silence=config.silence_ms,
                speech_noise_threshold=config.speech_noise_threshold,
                heartbeat=config.heartbeat,
            )
            await asyncio.to_thread(self._recognition.start)
        except Exception as exc:
            raise ASRError(str(exc)) from exc

    async def write(self, audio: AudioChunk) -> None:
        try:
            await asyncio.to_thread(self._recognition.send_audio_frame, audio.data)
        except Exception as exc:
            raise ASRError(str(exc)) from exc

    async def commit(self) -> None:
        """Finalize the locally detected utterance and close this request."""
        try:
            await asyncio.to_thread(self._recognition.stop)
        except Exception as exc:
            raise ASRError(str(exc)) from exc

    async def events(self) -> AsyncIterator[Transcript]:
        while (item := await self._queue.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._recognition is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._recognition.stop)
        await self._queue.put(None)
