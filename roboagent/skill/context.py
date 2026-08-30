"""Skill-specific model context transformation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from roboagent.runtime import CancellationToken, ModelContext
from roboagent.skill.skill import Skill


def format_skill_context(skills: Sequence[Skill]) -> str:
    active = [skill for skill in skills if skill.is_active]
    if not active: return ""
    lines = ["Available RoboAgent skills:"]
    for skill in active:
        parts = [f"- {skill.identity}: {skill.description}"]
        if skill.allowed_tools: parts.append(f"allowed_tools={','.join(skill.allowed_tools)}")
        if skill.prompt_template: parts.append(f"instructions={skill.prompt_template}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def create_skill_context_transform(skills: Sequence[Skill]):
    active = tuple(skill for skill in skills if skill.is_active)
    def transform(context: ModelContext, _cancellation: CancellationToken) -> ModelContext:
        if not (block := format_skill_context(active)): return context
        return replace(context, system_prompt=f"{context.system_prompt}\n\n{block}" if context.system_prompt else block)
    return transform
