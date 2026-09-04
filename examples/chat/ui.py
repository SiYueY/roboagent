"""Gradio presentation, browser-local state, and callbacks for the chat example."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STYLE_PATH = Path(__file__).with_name("style.css")
FRONTEND_PATH = Path(__file__).with_name("frontend.js")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gradio as gr

from roboagent.agent import Agent, AgentSession
from roboagent.runtime import AgentEvent, BytesSource, ImageContent, RunStatus, TextContent, UserMessage, modality
from roboagent.vision import VisionContext, VisionFrame

logger = logging.getLogger(__name__)

CHAT_THEME = gr.themes.Soft(primary_hue="orange", neutral_hue="slate", radius_size="lg")
ERROR_MESSAGE = "RoboAgent could not complete the request."
DEFAULT_TITLE = "新对话"
MAX_CONVERSATIONS = 20
TITLE_LIMIT = 24
MAX_CAMERA_SNAPSHOT_BYTES = 5 * 1024 * 1024
MAX_CAMERA_SNAPSHOT_WIDTH = 4096
MAX_CAMERA_SNAPSHOT_HEIGHT = 4096
MAX_CAMERA_SNAPSHOT_PIXELS = 16 * 1024 * 1024

ChatHistory = list[dict[str, str]]
ChatViewUpdate = tuple[ChatHistory, "ChatPageState", str, object, str]
BrowserAction = tuple[gr.components.Component, str]


def chat_launch_options() -> dict[str, object]:
    """Return Gradio application options that belong on ``Blocks.launch``."""
    frontend = FRONTEND_PATH.read_text(encoding="utf-8")
    return {
        "theme": CHAT_THEME,
        "css_paths": [STYLE_PATH],
        "head": f"<script>({frontend})()</script>",
    }


def bind_browser_action(component: gr.components.Component, method: str) -> None:
    """Connect a Gradio control to the browser-local frontend API."""
    component.click(
        fn=None,
        js=f"() => window.roboagentChat?.{method}()",
        queue=False,
    )


@dataclass(slots=True)
class BrowserConversation:
    """One browser-local conversation and its independent Agent transcript."""

    id: str
    session: AgentSession
    history: ChatHistory = field(default_factory=list)
    title: str = DEFAULT_TITLE
    updated_at: float = field(default_factory=time)
    voice_token: str = ""
    vision_context: VisionContext = field(default_factory=VisionContext)


@dataclass(slots=True)
class ChatPageState:
    """All conversations held by one browser page until it is refreshed."""

    conversations: list[BrowserConversation]
    active_id: str


def new_conversation(agent: Agent, speech_registry=None) -> BrowserConversation:
    """Create an empty conversation with its own AgentSession."""
    conversation = BrowserConversation(id=uuid4().hex, session=agent.new_session())
    if speech_registry is not None:
        conversation.voice_token = speech_registry.register(conversation)
    return conversation


def create_page_state(agent: Agent, speech_registry=None) -> ChatPageState:
    """Create the initial browser-local chat page state."""
    conversation = new_conversation(agent, speech_registry)
    return ChatPageState(conversations=[conversation], active_id=conversation.id)


def active_conversation(state: ChatPageState) -> BrowserConversation:
    """Return the selected conversation, repairing an invalid selection defensively."""
    for conversation in state.conversations:
        if conversation.id == state.active_id:
            return conversation
    conversation = max(state.conversations, key=lambda item: item.updated_at)
    state.active_id = conversation.id
    return conversation


def conversation_title(message: str) -> str:
    """Derive a compact sidebar title without an extra model request."""
    normalized = " ".join(message.split())
    if len(normalized) <= TITLE_LIMIT:
        return normalized or DEFAULT_TITLE
    return f"{normalized[:TITLE_LIMIT]}…"


def conversation_count(conversation: BrowserConversation) -> int:
    return sum(message["role"] == "user" for message in conversation.history)


def relative_time(timestamp: float) -> str:
    elapsed = max(0, int(time() - timestamp))
    if elapsed < 60:
        return "刚刚"
    if elapsed < 3600:
        return f"{elapsed // 60} 分钟前"
    if elapsed < 86400:
        return f"{elapsed // 3600} 小时前"
    return f"{elapsed // 86400} 天前"


def conversation_choices(state: ChatPageState) -> list[tuple[str, str]]:
    """Build newest-first labels for Gradio's selectable sidebar list."""
    ordered = sorted(state.conversations, key=lambda item: item.updated_at, reverse=True)
    return [
        (f"{item.title}\n{conversation_count(item)} 条消息 · {relative_time(item.updated_at)}", item.id)
        for item in ordered
    ]


def header_text(conversation: BrowserConversation) -> str:
    return f"## {conversation.title}\n{conversation_count(conversation)} 条消息"


def session_list_update(state: ChatPageState):
    return gr.update(choices=conversation_choices(state), value=state.active_id)


def view_update(conversation: BrowserConversation, state: ChatPageState) -> ChatViewUpdate:
    """Build the common component update emitted after a chat state change."""
    return list(conversation.history), state, "", session_list_update(state), header_text(conversation)


def enforce_conversation_limit(state: ChatPageState, speech_registry=None) -> None:
    """Remove the oldest non-active conversations when the page reaches its cap."""
    while len(state.conversations) > MAX_CONVERSATIONS:
        candidates = [item for item in state.conversations if item.id != state.active_id]
        if not candidates:
            return
        stale = min(candidates, key=lambda item: item.updated_at)
        state.conversations.remove(stale)
        if speech_registry is not None and stale.voice_token:
            speech_registry.discard(stale.voice_token)


def create_conversation(
    state: ChatPageState,
    agent: Agent,
    speech_registry=None,
) -> ChatViewUpdate:
    """Add and activate a fresh page-local conversation."""
    conversation = new_conversation(agent, speech_registry)
    state.conversations.append(conversation)
    state.active_id = conversation.id
    enforce_conversation_limit(state, speech_registry)
    return view_update(conversation, state)


def select_conversation(
    conversation_id: str | None,
    state: ChatPageState,
) -> ChatViewUpdate:
    """Switch the displayed history without recreating the selected AgentSession."""
    if conversation_id and any(item.id == conversation_id for item in state.conversations):
        state.active_id = conversation_id
    conversation = active_conversation(state)
    return view_update(conversation, state)


async def chat(
    message: str,
    _history: ChatHistory | None,
    state: ChatPageState,
    camera_snapshot: str = "",
) -> AsyncIterator[ChatViewUpdate]:
    """Run one turn against the active conversation and stream display-only history."""
    text = message.strip()
    conversation = active_conversation(state)
    if not text:
        yield view_update(conversation, state)
        return

    if not conversation.history:
        conversation.title = conversation_title(text)
    contents = [TextContent(text)]
    frame = _frame_from_data_url(camera_snapshot)
    display_text = text
    if frame is not None:
        image = ImageContent(BytesSource(frame.data), media_type=frame.mime_type)
        capabilities = conversation.session.agent.model.capabilities
        if modality(image) in capabilities.input_modalities:
            conversation.vision_context.update(frame)
            contents.append(image)
        else:
            display_text = f"{text}\n\n*当前模型不支持相机画面，已按文本发送。*"
    elif camera_snapshot:
        display_text = f"{text}\n\n*相机画面未能附加，已按文本发送。*"
    conversation.history = [
        *conversation.history,
        {"role": "user", "content": display_text},
        {"role": "assistant", "content": "正在思考…"},
    ]
    conversation.updated_at = time()
    run = conversation.session.start(UserMessage(contents))

    try:
        yield view_update(conversation, state)
        assistant_text = ""
        async for event in run.events():
            if isinstance(event, AgentEvent) and event.type == "model_delta" and event.text:
                assistant_text += event.text
                conversation.history = [
                    *conversation.history[:-1],
                    {"role": "assistant", "content": assistant_text},
                ]
                conversation.updated_at = time()
                yield view_update(conversation, state)

        result = await run.result()
        if result.status is not RunStatus.COMPLETED:
            logger.error("RoboAgent chat run failed: run_id=%s status=%s error=%s", run.run_id, result.status.value, result.error)
            conversation.history = [
                *conversation.history[:-1],
                {"role": "assistant", "content": ERROR_MESSAGE},
            ]
            yield view_update(conversation, state)
    except asyncio.CancelledError:
        run.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.shield(run.result())
        raise
    except Exception:
        logger.exception("RoboAgent chat handler failed")
        conversation.history = [
            *conversation.history[:-1],
            {"role": "assistant", "content": ERROR_MESSAGE},
        ]
        yield view_update(conversation, state)


def _frame_from_data_url(value: str) -> VisionFrame | None:
    try:
        snapshot = json.loads(value)
        data_url, browser_width, browser_height = snapshot["data_url"], snapshot["width"], snapshot["height"]
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(data_url, str) or not isinstance(browser_width, int) or not isinstance(browser_height, int):
        return None
    if browser_width <= 0 or browser_height <= 0 or not data_url.startswith("data:image/jpeg;base64,"):
        return None
    encoded = data_url.split(",", 1)[1]
    if len(encoded) > (MAX_CAMERA_SNAPSHOT_BYTES * 4 + 2) // 3:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, IndexError):
        return None
    if len(data) > MAX_CAMERA_SNAPSHOT_BYTES:
        return None
    dimensions = _jpeg_dimensions(data)
    if dimensions is None:
        return None
    width, height = dimensions
    if width > MAX_CAMERA_SNAPSHOT_WIDTH or height > MAX_CAMERA_SNAPSHOT_HEIGHT or width * height > MAX_CAMERA_SNAPSHOT_PIXELS:
        return None
    if (width, height) != (browser_width, browser_height):
        return None
    return VisionFrame(data=data, mime_type="image/jpeg", width=width, height=height, source="browser-camera")


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return None
    index = 2
    sof_markers = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
    while index < len(data):
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            return None
        marker = data[index]
        index += 1
        if marker == 0xD9:
            return None
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if marker in sof_markers:
            if length < 8:
                return None
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            components = data[index + 7]
            if not width or not height or not components or length != 8 + 3 * components:
                return None
            return width, height
        index += length
    return None


def create_demo(agent: Agent, speech_registry=None) -> gr.Blocks:
    """Build the browser-local multi-session chat page."""
    initial_state = create_page_state(agent, speech_registry)
    initial_conversation = active_conversation(initial_state)
    with gr.Blocks(
        title="RoboAgent",
        fill_height=True,
        fill_width=True,
    ) as demo:
        page_state = gr.State(value=initial_state)
        # Keep the token component rendered (but CSS-hidden): ``visible=False``
        # removes it from Gradio's DOM before the browser can open WebSocket.
        gr.Textbox(
            value=initial_conversation.voice_token,
            elem_id="voice-token",
            container=False,
            show_label=False,
        )
        with gr.Row(elem_id="workspace", equal_height=False):
            with gr.Column(elem_id="sidebar", scale=0, min_width=260):
                gr.Markdown("# RoboAgent\n轻量级机器人智能助手", elem_id="brand")
                new_button = gr.Button("＋ 新建对话", variant="secondary", size="sm", elem_id="new-chat")
                session_list = gr.Radio(
                    choices=conversation_choices(initial_state),
                    value=initial_state.active_id,
                    show_label=False,
                    container=False,
                    min_width=0,
                    elem_id="session-list",
                )
                gr.Markdown("Qwen3.7-Flash", elem_id="model-name")

            with gr.Column(elem_id="main-area", scale=1, min_width=0):
                with gr.Row(elem_id="chat-header", equal_height=True):
                    sidebar_toggle = gr.Button(
                        value="",
                        variant="secondary",
                        size="sm",
                        scale=0,
                        min_width=38,
                        elem_id="sidebar-toggle",
                    )
                    with gr.Column(scale=1, min_width=0):
                        title = gr.Markdown(header_text(initial_conversation), elem_id="conversation-title")
                camera_switch_button = gr.Button(
                    value="",
                    variant="secondary",
                    size="sm",
                    scale=0,
                    min_width=44,
                    elem_id="camera-switch-button",
                )
                with gr.Column(elem_id="conversation"):
                    chatbot = gr.Chatbot(
                        value=initial_conversation.history,
                        show_label=False,
                        layout="bubble",
                        render_markdown=True,
                        allow_tags=False,
                        elem_id="chatbot",
                    )
                    with gr.Column(elem_id="composer", scale=0, min_width=0):
                        textbox = gr.Textbox(
                            placeholder="给 RoboAgent 发送消息…",
                            show_label=False,
                            container=False,
                            lines=2,
                            max_lines=8,
                            elem_id="message-input",
                        )
                        camera_snapshot = gr.Textbox(value="", visible=False, elem_id="camera-snapshot")
                        with gr.Row(elem_id="composer-actions", equal_height=True):
                            gr.Markdown(
                                "Qwen3.7-Flash",
                                elem_id="composer-model",
                                container=False,
                            )
                            voice_call_button = gr.Button(
                                "语音通话",
                                variant="secondary",
                                size="sm",
                                scale=0,
                                min_width=0,
                                elem_id="voice-call-button",
                            )
                            send_button = gr.Button(
                                value="",
                                variant="primary",
                                size="sm",
                                scale=0,
                                min_width=44,
                                elem_id="send-button",
                            )
                    gr.Markdown(
                        "帮我检查机器人的当前状态……",
                        elem_id="voice-caption",
                        container=False,
                    )
                    with gr.Group(elem_id="voice-panel"):
                        gr.Markdown(
                            "点击麦克风开始",
                            elem_id="voice-status",
                            container=False,
                        )
                        with gr.Row(elem_id="voice-controls", equal_height=True):
                            voice_microphone = gr.Button("麦克风", scale=1, min_width=0, elem_id="voice-microphone")
                            voice_video = gr.Button("视频", scale=1, min_width=0, elem_id="voice-video")
                            voice_captions = gr.Button("字幕", scale=1, min_width=0, elem_id="voice-captions")
                            voice_speaker = gr.Button("扬声器", scale=1, min_width=0, elem_id="voice-speaker")
                            voice_hangup = gr.Button("挂断", scale=1, min_width=0, elem_id="voice-hangup")
                            gr.Button("更多", scale=1, min_width=0, interactive=False, elem_id="voice-more")
            mobile_sidebar_close = gr.Button(
                value="",
                variant="secondary",
                size="sm",
                scale=0,
                min_width=38,
                elem_id="mobile-sidebar-close",
            )

        chat_inputs: Sequence[gr.components.Component] = [textbox, chatbot, page_state, camera_snapshot]
        chat_outputs: Sequence[gr.components.Component] = [chatbot, page_state, textbox, session_list, title]
        snapshot_js = "(message, history, state, snapshot) => [message, history, state, window.roboagentChat?.captureCameraSnapshot() || '']"
        send_button.click(chat, inputs=chat_inputs, outputs=chat_outputs, js=snapshot_js)
        textbox.submit(chat, inputs=chat_inputs, outputs=chat_outputs, js=snapshot_js)
        new_button.click(
            lambda state: create_conversation(state, agent, speech_registry),
            inputs=page_state,
            outputs=chat_outputs,
        )
        session_list.change(select_conversation, inputs=[session_list, page_state], outputs=chat_outputs)
        browser_actions: tuple[BrowserAction, ...] = (
            (sidebar_toggle, "toggleSidebar"),
            (mobile_sidebar_close, "closeMobileSidebar"),
            (voice_call_button, "enterVoiceMode"),
            (voice_microphone, "toggleMicrophone"),
            (voice_video, "toggleCamera"),
            (voice_captions, "toggleCaptions"),
            (voice_speaker, "toggleSpeaker"),
            (voice_hangup, "exitVoiceMode"),
            (camera_switch_button, "switchCamera"),
        )
        for component, method in browser_actions:
            bind_browser_action(component, method)
    return demo
