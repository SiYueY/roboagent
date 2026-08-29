"""Builder for the native RoboAgent facade."""

from __future__ import annotations

from dataclasses import dataclass

from roboagent.agent.agent import Agent
from roboagent.agent.hooks import AfterToolCall, BeforeToolCall, ContextTransform
from roboagent.model.client import ChatModel
from roboagent.skill import Skill, SkillManager
from roboagent.tool import ResolutionContext, Tool, ToolManager


@dataclass(slots=True)
class AgentBuilder:
    """Assemble a native Agent from model, tools, skills, and hooks."""

    model: ChatModel
    tools: list[Tool] | None = None
    system_prompt: str | None = None
    context_transforms: list[ContextTransform] | None = None
    skills: list[Skill] | None = None
    name: str | None = None
    tool_manager: ToolManager | None = None
    skill_manager: SkillManager | None = None
    agent_id: str = "roboagent"
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    max_turns: int = 32

    def build(self) -> Agent:
        """Build the configured native Agent."""
        active_skills = self._resolve_active_skills()
        tools = self._resolve_tools(active_skills)

        return Agent(model=self.model, tools=tools, system_prompt=self.system_prompt,
                     context_transforms=self.context_transforms or (), before_tool_call=self.before_tool_call,
                     after_tool_call=self.after_tool_call, max_turns=self.max_turns)

    def _resolve_active_skills(self) -> list[Skill]:
        if self.skills is not None:
            candidates = self.skills
        elif self.skill_manager is not None:
            candidates = self.skill_manager.list_skills(enabled_only=True)
        else:
            candidates = []
        return [skill for skill in candidates if skill.is_active]

    def _resolve_tools(self, active_skills: list[Skill]) -> list[Tool]:
        resolved_tools = list(self.tools or [])
        if self.tool_manager is not None:
            context = ResolutionContext(agent_id=self.agent_id, activated_skills=tuple(active_skills))
            resolved_tools.extend(self.tool_manager.get_tools(context))
        return _dedupe_tools(resolved_tools)

def _dedupe_tools(tools: list[Tool]) -> list[Tool]:
    deduped: dict[str, Tool] = {}
    for tool in tools:
        if tool.name not in deduped: deduped[tool.name] = tool
    return list(deduped.values())

__all__ = ["AgentBuilder"]
