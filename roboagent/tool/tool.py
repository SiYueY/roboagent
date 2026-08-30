"""Native tools: schema, validation, and execution only."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from roboagent.runtime import CancellationToken, ModelContext, ToolCall, ToolDefinition, ToolExecutionResult
from roboagent.tool.errors import ToolRegistrationError
from roboagent.tool.schema import ToolSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Tool:
    """Runtime representation of one managed tool.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable summary for operators and the model.
        group: Logical grouping used for filtering.
        source: Logical source label such as `builtin` or `project`.
        visible_by_default: Whether the tool is directly bound without discovery.
        deferred: Whether the tool should be hidden from direct binding.
        allowed_agents: Optional allowlist of agent or subagent identifiers.
    """

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[[BaseModel, "ToolInvocation"], Any]
    group: str
    source: str
    visible_by_default: bool = True
    deferred: bool = False
    allowed_agents: tuple[str, ...] = ()

    @classmethod
    def from_spec(cls, spec: ToolSpec, parameters: type[BaseModel], handler: Callable[[BaseModel, "ToolInvocation"], Any]) -> Tool:
        """Build a runtime tool from a schema and validated spec.

        Args:
            spec: Validated metadata schema.

        Returns:
            A runtime `Tool` instance.

        Raises:
            ToolRegistrationError: If parameters or handler are invalid.
        """
        if not inspect.isclass(parameters) or not issubclass(parameters, BaseModel):
            raise ToolRegistrationError("Tool parameters must be a Pydantic BaseModel subclass.")
        if not callable(handler):
            raise ToolRegistrationError("Tool handler must be callable.")

        return cls(
            name=spec.name,
            description=spec.description,
            parameters=parameters,
            handler=handler,
            group=spec.group,
            source=spec.source,
            visible_by_default=spec.visible_by_default,
            deferred=spec.deferred,
            allowed_agents=spec.allowed_agents,
        )

    def is_available_to(self, principal_id: str) -> bool:
        """Return whether the tool is available to the given principal.

        Args:
            principal_id: Agent or subagent identifier.

        Returns:
            `True` if the tool is unrestricted or explicitly allows the
            provided principal.
        """
        return not self.allowed_agents or principal_id in self.allowed_agents

    def is_directly_visible(self) -> bool:
        """Return whether the tool should be directly bound to the model."""
        return self.visible_by_default and not self.deferred

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(self.name, self.description, self.parameters.model_json_schema())

    def validate(self, arguments: dict[str, Any] | None) -> BaseModel | str:
        try:
            return self.parameters.model_validate(arguments)
        except ValidationError as exc:
            return str(exc)

    async def execute(self, params: BaseModel, invocation: "ToolInvocation") -> ToolExecutionResult:
        if invocation.cancellation.cancelled:
            code = "timeout" if invocation.cancellation.reason == "timeout" else "cancelled"
            return ToolExecutionResult("Tool execution was cancelled.", is_error=True, error_code=code)
        try:
            value = self.handler(params, invocation)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, ToolExecutionResult):
                return value
            details = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            return ToolExecutionResult(value if isinstance(value, str) else json.dumps(details, ensure_ascii=False, default=str), details)
        except Exception:
            logger.exception(
                "tool handler failed: tool=%s run_id=%s turn=%s tool_call_id=%s",
                self.name,
                invocation.run_id,
                invocation.turn,
                invocation.tool_call.id,
            )
            return ToolExecutionResult("Tool execution failed.", is_error=True, error_code="execution_error")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    run_id: str
    turn: int
    tool_call: ToolCall
    context: ModelContext
    cancellation: CancellationToken


__all__ = ["Tool", "ToolInvocation"]
