"""Middleware for exposing active RoboAgent skills to the model."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Awaitable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

from roboagent.skill import Skill


class SkillContextMiddleware(AgentMiddleware):
    """Inject active skill instructions into model requests."""

    def __init__(self, skills: Sequence[Skill] = ()) -> None:
        self.skills = tuple(skill for skill in skills if skill.is_active)

    def build_skill_context(self) -> str:
        """Return the formatted skill context block."""
        return format_skill_context(self.skills)

    def apply_to_system_prompt(
        self,
        system_prompt: str | SystemMessage | None,
    ) -> str | SystemMessage | None:
        """Append skill context to an existing system prompt."""
        skill_block = self.build_skill_context()
        if not skill_block:
            return system_prompt
        if system_prompt is None:
            return skill_block
        if isinstance(system_prompt, SystemMessage):
            return SystemMessage(content=f"{system_prompt.content}\n\n{skill_block}")
        return f"{system_prompt}\n\n{skill_block}"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse | AIMessage],
    ) -> ModelResponse | AIMessage:
        system_message = self._augment_system_message(request.system_message)
        return handler(request.override(system_message=system_message))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse | AIMessage]],
    ) -> ModelResponse | AIMessage:
        system_message = self._augment_system_message(request.system_message)
        return await handler(request.override(system_message=system_message))

    def _augment_system_message(self, system_message: SystemMessage | None) -> SystemMessage | None:
        updated = self.apply_to_system_prompt(system_message)
        if isinstance(updated, SystemMessage) or updated is None:
            return updated
        return SystemMessage(content=updated)


def format_skill_context(skills: Sequence[Skill]) -> str:
    """Format active skills into a compact model-facing context block."""
    active_skills = [skill for skill in skills if skill.is_active]
    if not active_skills:
        return ""

    lines = ["Available RoboAgent skills:"]
    for skill in active_skills:
        parts = [f"- {skill.name}@{skill.version}: {skill.description}"]
        if skill.tags:
            parts.append(f"tags={','.join(skill.tags)}")
        if skill.allowed_tools:
            parts.append(f"allowed_tools={','.join(skill.allowed_tools)}")
        if skill.prompt_template:
            parts.append(f"instructions={skill.prompt_template}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


__all__ = ["SkillContextMiddleware", "format_skill_context"]
