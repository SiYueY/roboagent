"""Gradio presentation, browser-local state, and callbacks for the chat example."""

from __future__ import annotations

import asyncio
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
from roboagent.runtime import MessageDeltaEvent

logger = logging.getLogger(__name__)

ERROR_MESSAGE = "RoboAgent could not complete the request."
DEFAULT_TITLE = "新对话"
MAX_CONVERSATIONS = 20
TITLE_LIMIT = 24

ChatHistory = list[dict[str, str]]
ChatViewUpdate = tuple[ChatHistory, "ChatPageState", str, object, str]

@dataclass(slots=True)
class BrowserConversation:
    """One browser-local conversation and its independent Agent transcript."""

    id: str
    session: AgentSession
    history: ChatHistory = field(default_factory=list)
    title: str = DEFAULT_TITLE
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)


@dataclass(slots=True)
class ChatPageState:
    """All conversations held by one browser page until it is refreshed."""

    conversations: list[BrowserConversation]
    active_id: str


def new_conversation(agent: Agent) -> BrowserConversation:
    """Create an empty conversation with its own AgentSession."""
    return BrowserConversation(id=uuid4().hex, session=agent.new_session())


def create_page_state(agent: Agent) -> ChatPageState:
    """Create the initial browser-local chat page state."""
    conversation = new_conversation(agent)
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


def enforce_conversation_limit(state: ChatPageState) -> None:
    """Remove the oldest non-active conversations when the page reaches its cap."""
    while len(state.conversations) > MAX_CONVERSATIONS:
        candidates = [item for item in state.conversations if item.id != state.active_id]
        if not candidates:
            return
        stale = min(candidates, key=lambda item: item.updated_at)
        state.conversations.remove(stale)


def create_conversation(
    state: ChatPageState,
    agent: Agent,
) -> ChatViewUpdate:
    """Add and activate a fresh page-local conversation."""
    conversation = new_conversation(agent)
    state.conversations.append(conversation)
    state.active_id = conversation.id
    enforce_conversation_limit(state)
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
) -> AsyncIterator[ChatViewUpdate]:
    """Run one turn against the active conversation and stream display-only history."""
    text = message.strip()
    conversation = active_conversation(state)
    if not text:
        yield view_update(conversation, state)
        return

    if not conversation.history:
        conversation.title = conversation_title(text)
    conversation.history = [
        *conversation.history,
        {"role": "user", "content": text},
        {"role": "assistant", "content": "正在思考…"},
    ]
    conversation.updated_at = time()
    run = conversation.session.start(text)

    try:
        yield view_update(conversation, state)
        assistant_text = ""
        async for event in run.events():
            if isinstance(event, MessageDeltaEvent) and event.kind == "text":
                assistant_text += event.delta
                conversation.history = [
                    *conversation.history[:-1],
                    {"role": "assistant", "content": assistant_text},
                ]
                conversation.updated_at = time()
                yield view_update(conversation, state)

        result = await run.result()
        if result.status != "completed":
            logger.error("RoboAgent chat run failed: run_id=%s status=%s error=%s", result.run_id, result.status, result.error)
            conversation.history = [
                *conversation.history[:-1],
                {"role": "assistant", "content": ERROR_MESSAGE},
            ]
            yield view_update(conversation, state)
    except asyncio.CancelledError:
        run.cancel("user")
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


def create_demo(agent: Agent) -> gr.Blocks:
    """Build the browser-local multi-session chat page."""
    initial_state = create_page_state(agent)
    initial_conversation = active_conversation(initial_state)
    theme = gr.themes.Soft(primary_hue="orange", neutral_hue="slate", radius_size="lg")
    with gr.Blocks(
        title="RoboAgent",
        theme=theme,
        css_paths=[STYLE_PATH],
        js=FRONTEND_PATH.read_text(encoding="utf-8"),
        fill_height=True,
        fill_width=True,
    ) as demo:
        page_state = gr.State(value=initial_state)
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
                gr.Markdown("`Qwen3.7-Flash`", elem_id="model-name")

            with gr.Column(elem_id="main-area", scale=1, min_width=0):
                with gr.Row(elem_id="chat-header", equal_height=True):
                    gr.Button(
                        value="",
                        variant="secondary",
                        size="sm",
                        scale=0,
                        min_width=38,
                        elem_id="sidebar-toggle",
                    )
                    with gr.Column(scale=1, min_width=0):
                        title = gr.Markdown(header_text(initial_conversation), elem_id="conversation-title")
                with gr.Column(elem_id="conversation"):
                    chatbot = gr.Chatbot(
                        type="messages",
                        value=initial_conversation.history,
                        show_label=False,
                        layout="bubble",
                        render_markdown=True,
                        allow_tags=False,
                        elem_id="chatbot",
                    )
                    with gr.Group(elem_id="composer"):
                        textbox = gr.Textbox(
                            placeholder="给 RoboAgent 发送消息…",
                            show_label=False,
                            container=False,
                            lines=2,
                            max_lines=8,
                            elem_id="message-input",
                        )
                        with gr.Row(elem_id="composer-actions", equal_height=True):
                            gr.Markdown(
                                "Qwen3.7-Flash",
                                elem_id="composer-model",
                                container=False,
                            )
                            gr.Button(
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
                            gr.Button("麦克风", scale=1, min_width=0, elem_id="voice-microphone")
                            gr.Button("视频", scale=1, min_width=0, interactive=False, elem_id="voice-video")
                            gr.Button("字幕", scale=1, min_width=0, elem_id="voice-captions")
                            gr.Button("扬声器", scale=1, min_width=0, elem_id="voice-speaker")
                            gr.Button("挂断", scale=1, min_width=0, elem_id="voice-hangup")
                            gr.Button("更多", scale=1, min_width=0, interactive=False, elem_id="voice-more")

        chat_inputs: Sequence[gr.components.Component] = [textbox, chatbot, page_state]
        chat_outputs: Sequence[gr.components.Component] = [chatbot, page_state, textbox, session_list, title]
        send_button.click(chat, inputs=chat_inputs, outputs=chat_outputs)
        textbox.submit(chat, inputs=chat_inputs, outputs=chat_outputs)
        new_button.click(
            lambda state: create_conversation(state, agent),
            inputs=page_state,
            outputs=chat_outputs,
        )
        session_list.change(select_conversation, inputs=[session_list, page_state], outputs=chat_outputs)
    return demo
