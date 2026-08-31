"""DashScope realtime synthesis adapter."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

from ..config import DashScopeTTSConfig
from ..errors import TTSError
from ..types import AudioChunk, DEFAULT_OUTPUT_FORMAT


class DashScopeTTS:
    """Reuse one realtime WebSocket during a speech-session lifetime.

    DashScope's ``commit`` completes one response but does not close its
    session. Keeping the connection warm removes a WebSocket handshake and
    ``session.update`` round trip for every LLM text chunk.
    """

    def __init__(self, config: DashScopeTTSConfig) -> None:
        self.config = config
        self._client: Any | None = None
        self._response_queue: asyncio.Queue[bytes | Exception | None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _connect(self) -> None:
        if self._client is not None:
            return
        try:
            import dashscope
            from dashscope.audio.qwen_tts_realtime import AudioFormat, QwenTtsRealtime, QwenTtsRealtimeCallback
        except ImportError as exc:
            raise TTSError("DashScope speech support requires `pip install roboagent[speech]`.") from exc

        loop = asyncio.get_running_loop()
        owner = self

        class Callback(QwenTtsRealtimeCallback):
            def on_close(self, _status_code, _message) -> None:
                owner._client = None
                queue = owner._response_queue
                if queue is not None:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        TTSError("DashScope realtime TTS connection closed unexpectedly."),
                    )

            def on_event(self, response: Any) -> None:
                try:
                    event_type = _field(response, "type")
                    queue = owner._response_queue
                    if queue is None:
                        return
                    if event_type == "response.audio.delta":
                        loop.call_soon_threadsafe(queue.put_nowait, base64.b64decode(_field(response, "delta")))
                    elif event_type in {"response.done", "session.finished"}:
                        loop.call_soon_threadsafe(queue.put_nowait, None)
                except Exception as exc:
                    queue = owner._response_queue
                    if queue is not None:
                        loop.call_soon_threadsafe(queue.put_nowait, TTSError(str(exc)))

        if api_key := (self.config.api_key or os.getenv("DASHSCOPE_API_KEY")):
            dashscope.api_key = api_key
        last_error: Exception | None = None
        for attempt in range(2):
            client = QwenTtsRealtime(
                model=self.config.model,
                callback=Callback(),
                workspace=self.config.workspace_id,
                url=_tts_url(self.config.region),
            )
            try:
                await asyncio.to_thread(client.connect)
                await asyncio.to_thread(
                    client.update_session,
                    voice=self.config.voice,
                    response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    mode="commit",
                    language_type=self.config.language,
                    volume=self.config.volume,
                )
            except Exception as exc:
                last_error = exc
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(client.close)
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                break
            self._client = client
            return
        raise TTSError(f"DashScope realtime TTS connection failed: {last_error}") from last_error

    async def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        if not text.strip():
            return
        async with self._lock:
            if self._closed:
                raise TTSError("DashScope TTS session is closed.")
            await self._connect()
            assert self._client is not None
            queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
            self._response_queue = queue
            try:
                await asyncio.to_thread(self._client.clear_appended_text)
                await asyncio.to_thread(self._client.append_text, text)
                await asyncio.to_thread(self._client.commit)
                while (item := await queue.get()) is not None:
                    if isinstance(item, Exception):
                        raise item
                    yield AudioChunk(item, DEFAULT_OUTPUT_FORMAT)
            except TTSError:
                raise
            except Exception as exc:
                raise TTSError(str(exc)) from exc
            finally:
                if self._response_queue is queue:
                    self._response_queue = None

    async def cancel(self) -> None:
        """Cancel the active response while retaining the warm connection."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._client.cancel_response)

    async def close(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        self._response_queue = None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(client.close)


def _tts_url(region: str) -> str:
    return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime" if region == "singapore" else "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
