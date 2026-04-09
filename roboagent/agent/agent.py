
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langchain.agents import create_agent

from roboagent.skill import Skill

logger = logging.getLogger(__name__)

def create_roboagent(
    model: BaseChatModel,
    tools: list[BaseTool] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middlewares: list[AgentMiddleware] | None = None,
    skills: list[Skill] | None = None,
    name: str | None = None,
) -> CompiledStateGraph:
    """
    Create a new RoboAgent instance.

    Args:
        model: The language model to use.
        tools: Additional tools the agent should have access to.
        system_prompt: Custom system instructions to prepend before the base agent prompt.
        middlewares: Additional middleware to apply after the base stack but before the tail middleware.
        skills: Additional skills the agent should have access to.
        name: The name of the agent.

    Returns:
        A configured RoboAgent instance.
    """
    # TODO: Skill to str
    # if not skills:

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middlewares=middlewares,
        skills=skills,
        name=name,
    )
