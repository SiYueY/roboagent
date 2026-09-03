"""Turn-local immutable Tool resolution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Sequence
from roboagent.runtime.types import RunContext, ToolDefinition
from .tool import Tool
@dataclass(frozen=True, slots=True)
class FrozenToolSet:
    tools: tuple[Tool, ...]
    def __post_init__(self) -> None:
        names = tuple(tool.name for tool in self.tools)
        if len(names) != len(set(names)):
            raise ValueError("FrozenToolSet requires unique tool names.")
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: return tuple(tool.definition for tool in self.tools)
    def by_name(self) -> dict[str, Tool]: return {tool.name:tool for tool in self.tools}
class ToolResolver(Protocol):
    async def resolve(self, run_context: RunContext, tools: Sequence[Tool]) -> FrozenToolSet: ...
class StaticToolResolver:
    async def resolve(self, run_context: RunContext, tools: Sequence[Tool]) -> FrozenToolSet:
        run_context.cancellation.throw_if_cancelled()
        return FrozenToolSet(tuple(tools))
