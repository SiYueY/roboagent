"""Provider-neutral tool contract; scheduling belongs to AgentExecutor."""
from __future__ import annotations
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping
from roboagent.message import MediaLimits, MessageContent, ProtocolError, ToolCall, ToolExecutionError, freeze_json, normalize_content
from roboagent.runtime.types import CancellationToken, ModelContext, RunContext, ToolDefinition


class InvalidToolOutputError(ValueError):
    """A handler returned data that cannot become canonical ToolOutput."""

@dataclass(frozen=True, slots=True)
class ToolOutput:
    content: tuple[MessageContent, ...] = ()
    is_error: bool = False
    error_code: str | None = None
    details: Any = None
    def __init__(self, content: Iterable[MessageContent] | str = (), is_error: bool = False, error_code: str | None = None, details: Any = None, *, limits: MediaLimits = MediaLimits()) -> None:
        if not isinstance(is_error, bool) or error_code is not None and not isinstance(error_code, str):
            raise TypeError("Invalid ToolOutput metadata.")
        if error_code is not None:
            try:
                ToolExecutionError(error_code, "validation")
            except ProtocolError as exc:
                raise ValueError("ToolOutput.error_code must be a safe error identifier.") from exc
        object.__setattr__(self, "content", normalize_content(content, limits))
        object.__setattr__(self, "is_error", is_error)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "details", details)
@dataclass(slots=True)
class ToolCallContext:
    call_id: str; cancellation: CancellationToken
@dataclass(frozen=True, slots=True)
class ToolInvocation:
    call: ToolCall; run_context: RunContext; tool_context: ToolCallContext; model_context: ModelContext | None = None
@dataclass(frozen=True, slots=True)
class Tool:
    name: str; description: str; parameters: Mapping[str, Any]; handler: Callable[[Mapping[str, Any], ToolInvocation], Any]
    expose_model_context: bool = False
    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str):
            raise ValueError("Tool name must be non-empty str and description must be str.")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("Tool parameters must be a JSON object schema.")
        frozen = freeze_json(self.parameters)
        if not isinstance(frozen, Mapping):
            raise TypeError("Tool parameters must be a JSON object schema.")
        object.__setattr__(self, "parameters", frozen)
        if not callable(self.handler): raise TypeError("Tool handler must be callable.")
        if not isinstance(self.expose_model_context, bool):
            raise TypeError("Tool.expose_model_context must be bool.")
    @property
    def definition(self) -> ToolDefinition: return ToolDefinition(self.name, self.description, self.parameters)
    def validate(self, arguments: Mapping[str, Any] | None) -> Mapping[str, Any] | ToolExecutionError:
        if not isinstance(arguments, Mapping): return ToolExecutionError("invalid_arguments", "Tool arguments must be an object.")
        return arguments
    async def invoke(self, params: Mapping[str, Any], invocation: ToolInvocation, *, limits: object) -> ToolOutput:
        value = self.handler(params, invocation)
        if inspect.isawaitable(value): value = await value
        try:
            if isinstance(value, ToolOutput):
                return ToolOutput(value.content, value.is_error, value.error_code, value.details, limits=limits)
            if isinstance(value, str): return ToolOutput(value, limits=limits)
        except (TypeError, ValueError) as exc:
            raise InvalidToolOutputError("Tool output is not canonical MessageContent.") from exc
        raise InvalidToolOutputError("Tool handlers must return ToolOutput or str.")
