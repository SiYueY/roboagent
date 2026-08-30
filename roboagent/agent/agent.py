"""Immutable Agent definition."""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Sequence
from roboagent.agent.types import AgentHooks
from roboagent.model.client import ChatModel
from roboagent.runtime import Message
from roboagent.tool import Tool

@dataclass(frozen=True, slots=True)
class Agent:
    model: ChatModel; tools: tuple[Tool, ...]; system_prompt: str | None; hooks: AgentHooks; max_turns: int
    def __init__(self, model: ChatModel, *, tools: Sequence[Tool] = (), system_prompt: str | None = None, hooks: AgentHooks | None = None, max_turns: int = 32) -> None:
        if max_turns < 1: raise ValueError("max_turns must be positive.")
        object.__setattr__(self,"model",model); object.__setattr__(self,"tools",tuple(tools)); object.__setattr__(self,"system_prompt",system_prompt); object.__setattr__(self,"hooks",hooks or AgentHooks()); object.__setattr__(self,"max_turns",max_turns)
    def new_session(self, messages: Sequence[Message] = (), *, session_id: str | None = None):
        from roboagent.agent.session import AgentSession
        return AgentSession(self, messages, session_id)
