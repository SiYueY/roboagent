from __future__ import annotations

import asyncio
import inspect
import importlib.util
import sys
import unittest
from pathlib import Path

from roboagent.agent import Agent
from roboagent.runtime import AssistantMessage, ModelEvent


class ScriptedModel:
    model_name = "test-model"

    async def stream(self, _request, _cancellation):
        yield ModelEvent("start")
        yield ModelEvent("text_delta", delta="测试回复")
        yield ModelEvent("done", message=AssistantMessage("测试回复"))


class CancellableModel:
    model_name = "test-model"

    async def stream(self, _request, cancellation):
        yield ModelEvent("start")
        while not cancellation.cancelled:
            await asyncio.sleep(0)
        yield ModelEvent("cancelled")


@unittest.skipUnless(importlib.util.find_spec("gradio"), "requires the gradio optional extra")
class ChatExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).parents[2] / "examples" / "chat" / "ui.py"
        spec = importlib.util.spec_from_file_location("chat_example_ui", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.app = module

    def test_create_demo_has_session_sidebar_voice_ui_and_external_assets(self) -> None:
        demo = self.app.create_demo(Agent(ScriptedModel()))
        self.assertIsInstance(demo, self.app.gr.Blocks)
        stylesheet = self.app.STYLE_PATH.read_text()
        frontend = self.app.FRONTEND_PATH.read_text()
        self.assertIn("#session-list", stylesheet)
        self.assertIn("#composer", stylesheet)
        self.assertIn("#voice-panel", stylesheet)
        self.assertIn("#voice-controls", stylesheet)
        self.assertIn("voice-mode", stylesheet)
        self.assertIn("#sidebar-toggle", stylesheet)
        self.assertIn("#message-input", stylesheet)
        self.assertIn("sidebar-collapsed", stylesheet)
        self.assertIn("button#send-button", stylesheet)
        self.assertIn("button#sidebar-toggle", stylesheet)
        self.assertIn("max-height: none", stylesheet)
        self.assertIn("overflow: auto hidden", stylesheet)
        self.assertIn("#composer-actions .wrap", stylesheet)
        self.assertIn("#message-input textarea:focus", stylesheet)
        self.assertIn("height: 36px !important", stylesheet)
        self.assertIn("min-height: 72px", stylesheet)
        self.assertIn("::-webkit-scrollbar-button", stylesheet)
        self.assertIn("#composer-actions :is(.block, .form, .wrap, .styler)", stylesheet)
        self.assertIn("getUserMedia", frontend)
        self.assertIn("AudioContext", frontend)
        self.assertIn("voice-call-button", frontend)
        self.assertIn("sidebar-collapsed", frontend)

    def test_layout_uses_fixed_button_scales_and_client_sidebar_state(self) -> None:
        source = inspect.getsource(self.app.create_demo)
        self.assertIn("scale=0", source)
        self.assertIn("min_width=38", source)
        self.assertIn("min_width=44", source)
        self.assertIn("lines=2", source)
        self.assertIn("max_lines=8", source)
        self.assertNotIn("icon=", source)
        self.assertNotIn("value=None", source)
        self.assertIn("FRONTEND_PATH.read_text", source)
        self.assertNotIn("CHAT_LAYOUT_JS", source)
        self.assertIn("voice-call-button", source)
        self.assertNotIn("gr.HTML", source)
        self.assertNotIn("<button", source)
        self.assertNotIn("<svg", source)
        self.assertIn("voice-panel", source)
        self.assertIn("voice-microphone", source)
        self.assertIn("send_button.click(chat", source)
        self.assertIn("textbox.submit(chat", source)
        self.assertNotIn("async def send", source)
        self.assertNotIn("container=False,\n                    scale=1,", source)

    def test_example_server_uses_fast_interrupt_shutdown(self) -> None:
        chat_directory = Path(__file__).parents[2] / "examples" / "chat"
        source = (chat_directory / "app.py").read_text()
        certificate_script = (chat_directory / "generate_cert.sh").read_text()
        self.assertIn("prevent_thread_lock=True", source)
        self.assertIn("demo.server.force_exit = True", source)
        self.assertIn("demo.block_thread()", source)
        self.assertIn("ssl_certfile", source)
        self.assertIn("ssl_keyfile", source)
        self.assertIn('"ssl_verify": False', source)
        self.assertIn("CERTIFICATE_PATH.is_file()", source)
        self.assertIn("subjectAltName=IP:$lan_ip", certificate_script)
        self.assertIn("refusing to overwrite", certificate_script)

    def test_title_is_first_message_and_is_truncated(self) -> None:
        self.assertEqual(self.app.conversation_title("  一个   标题 "), "一个 标题")
        self.assertEqual(
            self.app.conversation_title("x" * (self.app.TITLE_LIMIT + 1)),
            "x" * self.app.TITLE_LIMIT + "…",
        )

    def test_new_select_and_limit_conversations(self) -> None:
        agent = Agent(ScriptedModel())
        state = self.app.create_page_state(agent)
        history, returned_state, textbox, session_update, title = self.app.view_update(
            self.app.active_conversation(state), state
        )
        self.assertEqual(history, [])
        self.assertIs(returned_state, state)
        self.assertEqual(textbox, "")
        self.assertEqual(session_update["value"], state.active_id)
        self.assertIn("新对话", title)
        first_id = state.active_id
        _, state, _, _, _ = self.app.create_conversation(state, agent)
        second_id = state.active_id
        self.assertNotEqual(first_id, second_id)
        _, state, _, _, _ = self.app.select_conversation(first_id, state)
        self.assertEqual(state.active_id, first_id)

        for _ in range(self.app.MAX_CONVERSATIONS + 2):
            self.app.create_conversation(state, agent)
        self.assertEqual(len(state.conversations), self.app.MAX_CONVERSATIONS)
        self.assertIn(state.active_id, [item.id for item in state.conversations])

    def test_streaming_and_session_histories_are_isolated(self) -> None:
        async def check() -> None:
            agent = Agent(ScriptedModel())
            state = self.app.create_page_state(agent)
            first_id = state.active_id
            first = [update async for update in self.app.chat("第一条会话", None, state)]
            self.assertEqual(first[-1][0][-1]["content"], "测试回复")
            self.assertEqual(self.app.active_conversation(state).title, "第一条会话")

            _, state, _, _, _ = self.app.create_conversation(state, agent)
            second_id = state.active_id
            second = [update async for update in self.app.chat("第二条会话", None, state)]
            self.assertEqual(second[-1][0][-1]["content"], "测试回复")

            history, state, _, _, _ = self.app.select_conversation(first_id, state)
            self.assertEqual(state.active_id, first_id)
            self.assertEqual(len(history), 2)
            self.assertNotEqual(first_id, second_id)

        asyncio.run(check())

    def test_cancelling_chat_requests_agent_run_cancellation(self) -> None:
        async def check() -> None:
            state = self.app.create_page_state(Agent(CancellableModel()))
            stream = self.app.chat("等待", None, state)
            await anext(stream)
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertFalse(self.app.active_conversation(state).session._active)

        asyncio.run(check())
