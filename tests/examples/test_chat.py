from __future__ import annotations

import asyncio
import base64
import inspect
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from roboagent.agent import Agent
from roboagent.message import AssistantMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
)
from roboagent.runtime import Modality

try:
    import gradio as _gradio

    _HAS_GRADIO6 = hasattr(_gradio, "themes")
except ImportError:
    _HAS_GRADIO6 = False


class ScriptedModel:
    model_name = "test-model"
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False
    )

    async def stream(self, context, settings=None):
        yield ResponseStarted("response", 0)
        yield TextDelta(1, "测试回复")
        yield ResponseCompleted(
            2, ModelResponse(AssistantMessage("测试回复"), FinishReason.STOP)
        )


class CancellableModel:
    model_name = "test-model"
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), False, False
    )

    async def stream(self, context, settings=None):
        yield ResponseStarted("response", 0)
        await asyncio.Event().wait()


class RecordingModel:
    model_name = "test-model"
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT, Modality.IMAGE}),
        frozenset({Modality.TEXT}),
        False,
        False,
    )

    def __init__(self) -> None:
        self.request = None

    async def stream(self, context, settings=None):
        self.request = context
        yield ResponseStarted("response", 0)
        yield ResponseCompleted(
            1, ModelResponse(AssistantMessage("seen"), FinishReason.STOP)
        )


class TextOnlyRecordingModel(RecordingModel):
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}),
        frozenset({Modality.TEXT}),
        False,
        False,
    )


@unittest.skipUnless(_HAS_GRADIO6, "requires the gradio>=6 optional extra")
class ChatExampleTests(unittest.TestCase):
    BROWSER_ACTIONS = (
        "toggleSidebar",
        "closeMobileSidebar",
        "enterVoiceMode",
        "toggleMicrophone",
        "toggleCamera",
        "toggleCaptions",
        "toggleSpeaker",
        "exitVoiceMode",
        "switchCamera",
    )

    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).parents[2] / "examples" / "chat" / "ui.py"
        spec = importlib.util.spec_from_file_location("chat_example_ui", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.app = module

    def test_gradio6_layout_and_browser_media_contracts(self) -> None:
        demo = self.app.create_demo(Agent(ScriptedModel()))
        self.addCleanup(demo.close)
        self.assertIsInstance(demo, self.app.gr.Blocks)
        config = demo.get_config_file()
        stylesheet = self.app.STYLE_PATH.read_text()
        frontend = self.app.FRONTEND_PATH.read_text()
        launch_options = self.app.chat_launch_options()

        self.assertEqual(launch_options["theme"], self.app.CHAT_THEME)
        self.assertEqual(launch_options["css_paths"], [self.app.STYLE_PATH])
        self.assertIn(frontend, launch_options["head"])
        self.assertTrue(launch_options["head"].startswith("<script>"))
        self.assertNotIn("js", launch_options)

        components = {
            component["props"].get("elem_id"): component
            for component in config["components"]
            if component["props"].get("elem_id")
        }
        self.assertEqual(components["composer"]["type"], "column")
        self.assertEqual(
            sum(
                item["props"].get("elem_id") == "composer"
                for item in config["components"]
            ),
            1,
        )
        self.assertEqual(components["message-input"]["props"]["lines"], 2)
        self.assertEqual(components["message-input"]["props"]["max_lines"], 8)
        self.assertEqual(components["voice-video"]["props"]["interactive"], True)
        self.assertEqual(components["voice-more"]["props"]["interactive"], False)

        browser_javascript = [
            dependency["js"]
            for dependency in config["dependencies"]
            if dependency.get("js")
        ]
        action_javascript = [
            item for item in browser_javascript if item.startswith("() =>")
        ]
        self.assertEqual(
            action_javascript,
            [
                f"() => window.roboagentChat?.{action}()"
                for action in self.BROWSER_ACTIONS
            ],
        )
        self.assertTrue(
            any("captureCameraSnapshot" in item for item in browser_javascript)
        )

        for selector in (
            "#session-list",
            "#sidebar",
            "#composer",
            "#voice-panel",
            "#voice-caption.is-visible",
            "#voice-controls",
            "#camera-layer",
            "#camera-switch-button",
            "#mobile-sidebar-close",
        ):
            self.assertIn(selector, stylesheet)
        self.assertNotIn("height: calc(100vh - 48px)", stylesheet)
        for contract in (
            "--voice-control-active-bg",
            "--voice-control-inactive-bg",
            "sidebar-collapsed",
            "camera-enabled",
            "label.show_textbox_border",
            "-webkit-tap-highlight-color: transparent",
            "overscroll-behavior: contain",
            "margin: auto 0 24px",
        ):
            self.assertIn(contract, stylesheet)

        for contract in (
            'document.querySelector("gradio-app")?.shadowRoot ?? document',
            "new MutationObserver",
            "window.roboagentChat",
            "window.roboagentChatInitError",
            "getUserMedia",
            "CAMERA_PROFILES",
            "frameRate: { ideal: 20, max: 24 }",
            "waitForCameraRelease",
            "waitForCameraMetadata",
            "cameraVideo.srcObject = null",
            "releaseStream",
            'window.addEventListener("pagehide"',
            "AudioContext",
            "ensureAudioOutput",
            "TTS_OUTPUT_GAIN = 1.8",
            "AUDIO_WORKLET_SOURCE",
            "URL.createObjectURL",
            'payload.type === "audio.completed"',
            'payload.type === "response.delta"',
            "state.responseCaption += payload.delta",
            'setStatus("正在生成回答…")',
            'setStatus("正在播放…")',
            "playbackSources: new Set()",
            "const clearPlayback",
            "const syncPlaybackStatus",
            "state.responseCompleted = false",
            "source.stop()",
            'type: "playback.started"',
            'payload.type === "playback.begin"',
            "activeResponseId",
            "speechMetrics",
            "getSpeechMetrics",
            "captureCameraSnapshot",
        ):
            self.assertIn(contract, frontend)
        for action in self.BROWSER_ACTIONS:
            self.assertIn(action, frontend)
        self.assertNotIn('"/roboagent-audio-worklet.js"', frontend)
        self.assertNotIn("playTone", frontend)
        self.assertNotIn("tonePlayed", frontend)
        self.assertEqual(
            [path.name for path in self.app.FRONTEND_PATH.parent.glob("*.js")],
            ["frontend.js"],
        )

    def test_layout_uses_gradio6_components_and_browser_action_helper(self) -> None:
        source = inspect.getsource(self.app.create_demo)
        self.assertIn("scale=0", source)
        self.assertIn("min_width=38", source)
        self.assertIn("min_width=44", source)
        self.assertIn("lines=2", source)
        self.assertIn("max_lines=8", source)
        self.assertNotIn("icon=", source)
        self.assertNotIn("value=None", source)
        self.assertNotIn("FRONTEND_PATH.read_text", source)
        self.assertNotIn("theme=", source)
        self.assertNotIn("css_paths=", source)
        self.assertIn("bind_browser_action(component, method)", source)
        self.assertIn("browser_actions", source)
        self.assertIn(
            "window.roboagentChat?.{method}()",
            inspect.getsource(self.app.bind_browser_action),
        )
        self.assertNotIn('type="messages"', source)
        self.assertNotIn("CHAT_LAYOUT_JS", source)
        self.assertIn("voice-call-button", source)
        self.assertIn('gr.Column(elem_id="composer"', source)
        self.assertNotIn("gr.HTML", source)
        self.assertNotIn("<button", source)
        self.assertNotIn("<svg", source)
        self.assertIn("voice-panel", source)
        self.assertIn("voice-microphone", source)
        self.assertIn("voice-video", source)
        self.assertIn("camera-switch-button", source)
        self.assertIn("mobile-sidebar-close", source)
        self.assertNotIn('interactive=False, elem_id="voice-video"', source)
        self.assertIn("send_button.click(chat", source)
        self.assertIn("textbox.submit(chat", source)
        self.assertNotIn("async def send", source)
        self.assertNotIn("container=False,\n                    scale=1,", source)

    def test_example_server_uses_mounted_gradio_and_has_no_legacy_worklet_route(
        self,
    ) -> None:
        chat_directory = Path(__file__).parents[2] / "examples" / "chat"
        source = (chat_directory / "app.py").read_text()
        certificate_script = (chat_directory / "generate_cert.sh").read_text()
        self.assertIn("gr.mount_gradio_app", source)
        self.assertIn("uvicorn.run", source)
        self.assertNotIn("prevent_thread_lock", source)
        self.assertNotIn("demo.server.force_exit", source)
        self.assertNotIn("demo.block_thread", source)
        self.assertNotIn("FileResponse", source)
        self.assertNotIn("audio-worklet.js", source)
        self.assertNotIn("def create_agent", source)
        self.assertFalse((chat_directory / "audio-worklet.js").exists())
        self.assertIn("chat_launch_options", source)
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

    def test_camera_snapshot_becomes_verified_image_content(self) -> None:
        jpeg = bytes.fromhex("ffd8ffc00011080002000303011100021100031100ffd9")
        snapshot = json.dumps(
            {
                "data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                "width": 3,
                "height": 2,
            }
        )
        model = RecordingModel()
        state = self.app.create_page_state(Agent(model))

        async def check() -> None:
            async for _ in self.app.chat("what is this?", None, state, snapshot):
                pass

        asyncio.run(check())
        assert model.request is not None
        contents = model.request.messages[-1].content
        self.assertEqual(contents[0].text, "what is this?")
        self.assertEqual(contents[1].source.data, jpeg)
        frame = self.app.active_conversation(state).vision_context.latest()
        self.assertEqual((frame.width, frame.height), (3, 2))

    def test_camera_snapshot_rejects_invalid_or_mismatched_input(self) -> None:
        self.assertIsNone(self.app._frame_from_data_url("not-json"))
        jpeg = bytes.fromhex("ffd8ffc00011080002000303011100021100031100ffd9")
        snapshot = json.dumps(
            {
                "data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                "width": 2,
                "height": 2,
            }
        )
        self.assertIsNone(self.app._frame_from_data_url(snapshot))

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
            first = [
                update async for update in self.app.chat("第一条会话", None, state)
            ]
            self.assertEqual(first[-1][0][-1]["content"], "测试回复")
            self.assertEqual(self.app.active_conversation(state).title, "第一条会话")

            _, state, _, _, _ = self.app.create_conversation(state, agent)
            second_id = state.active_id
            second = [
                update async for update in self.app.chat("第二条会话", None, state)
            ]
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
            self.assertIsNone(self.app.active_conversation(state).session.active_run_id)

        asyncio.run(check())
