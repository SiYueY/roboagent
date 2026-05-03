"""Builder for assembling RoboAgent runtime graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from roboagent.skill import Skill, SkillManager
from roboagent.tool import ResolutionContext, ToolManager


@dataclass(slots=True)
class AgentBuilder:
    """Assemble a RoboAgent graph from model, tools, skills, and middleware."""

    model: BaseChatModel
    tools: list[BaseTool] | None = None
    system_prompt: str | SystemMessage | None = None
    middlewares: list[AgentMiddleware] | None = None
    skills: list[Skill] | None = None
    name: str | None = None
    tool_manager: ToolManager | None = None
    skill_manager: SkillManager | None = None
    agent_id: str = "roboagent"
    extra_create_agent_kwargs: dict[str, Any] = field(default_factory=dict)

    def build(self) -> CompiledStateGraph:
        """Build and return the configured LangGraph agent."""
        active_skills = self._resolve_active_skills()
        tools = self._resolve_tools(active_skills)

        return create_agent(
            model=self.model,
            tools=tools,
            system_prompt=self.system_prompt,
            middleware=self.middlewares or (),
            name=self.name,
            **self.extra_create_agent_kwargs,
        )

    def _resolve_active_skills(self) -> list[Skill]:
        if self.skills is not None:
            candidates = self.skills
        elif self.skill_manager is not None:
            candidates = self.skill_manager.list_skills(enabled_only=True)
        else:
            candidates = []
        return [skill for skill in candidates if skill.is_active]

    def _resolve_tools(self, active_skills: list[Skill]) -> list[BaseTool] | None:
        resolved_tools = list(self.tools or [])
        if self.tool_manager is not None:
            context = ResolutionContext(agent_id=self.agent_id, activated_skills=tuple(active_skills))
            resolved_tools.extend(self.tool_manager.get_tools(context))
        if not resolved_tools:
            return None
        return _dedupe_tools(resolved_tools)

def _dedupe_tools(tools: list[BaseTool]) -> list[BaseTool]:
    deduped: dict[str, BaseTool] = {}
    for tool in tools:
        name = getattr(tool, "name", None)
        if isinstance(name, str) and name not in deduped:
            deduped[name] = tool
    return list(deduped.values())

__all__ = ["AgentBuilder"]
