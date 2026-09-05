"""Opt-in real-provider smoke test; it never runs in the default suite."""
from __future__ import annotations

import os
import struct
import unittest
import zlib

from roboagent.agent import Agent, RunConfig
from roboagent.message import (AssistantMessage, BytesSource, FrozenJsonObject, ImageContent,
    TextContent, ToolCall, ToolResultMessage, ToolResultStatus, UserMessage)
from roboagent.model.client import OpenAICompatibleModel


_LIVE = os.getenv("ROBOAGENT_LIVE_PROVIDER_TEST") == "1" and bool(os.getenv("DASHSCOPE_API_KEY"))

def _png() -> bytes:
    """Create a valid 32×32 RGB PNG above provider minimum-image limits."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + (b"\x00\x80\xff" * 32) for _ in range(32))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


_PNG = _png()


def _model() -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        model_name="qwen3.7-flash",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0,
        max_tokens=64,
        request_timeout=30,
    )


@unittest.skipUnless(_LIVE, "set ROBOAGENT_LIVE_PROVIDER_TEST=1 and DASHSCOPE_API_KEY to run")
class TongyiLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_stream_completes(self):
        result = await Agent(_model()).new_session().run(
            UserMessage("Reply with exactly: OK"),
            config=RunConfig(timeout=45),
        )
        self.assertEqual(
            result.status.value,
            "completed",
            f"error={result.error!r}",
        )
        self.assertIsNotNone(result.output)

    async def test_text_and_image_stream_completes(self):
        session = Agent(_model()).new_session()
        result = await session.run(
            UserMessage((
                TextContent("Reply with exactly: OK. Ignore the image content."),
                ImageContent(BytesSource(_PNG), "image/png"),
            ), limits=session.agent.media_limits),
            config=RunConfig(timeout=45),
        )
        self.assertEqual(result.status.value, "completed", repr(result.error))
        self.assertIsNotNone(result.output)

    async def test_image_tool_result_stream_completes(self):
        call = ToolCall("image_call", "return_image", FrozenJsonObject())
        session = Agent(_model()).new_session((
            UserMessage("Inspect the tool result and reply with exactly: OK."),
            AssistantMessage(tool_calls=(call,)),
            ToolResultMessage(
                "image_call", "return_image", ToolResultStatus.SUCCESS,
                (ImageContent(BytesSource(_PNG), "image/png"),),
            ),
        ))
        result = await session.run(config=RunConfig(timeout=45))
        self.assertEqual(result.status.value, "completed", repr(result.error))
        self.assertIsNotNone(result.output)


if __name__ == "__main__":
    unittest.main()
