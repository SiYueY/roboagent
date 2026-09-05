"""Canonical tool contracts, registry, results, and effect records."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from roboagent.message import (
    FrozenJsonObject,
    JsonValue,
    ToolCall,
    freeze_json,
    freeze_json_object,
)
from roboagent.runtime.types import CancellationToken, RunError

_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class ToolRegistrationError(ValueError):
    pass


class ToolExecutionFailure(Exception):
    """An ordinary model-visible failure reported by a Tool implementation."""

    def __init__(self, error: "ToolErrorInfo") -> None:
        self.error = error
        super().__init__(error.message)


class ToolEffectUnknown(ToolExecutionFailure):
    """The Tool started but cannot determine whether its side effect occurred."""


class ToolContractError(TypeError):
    """A Tool implementation violated the canonical Tool contract."""


class ToolExecutionMode(Enum):
    SERIAL = "serial"
    CONCURRENT = "concurrent"


class ToolEffectKind(Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECTING = "side_effecting"


class ToolDecision(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    FAIL_RUN = "fail_run"


class ToolEffectStatus(Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: FrozenJsonObject

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME.fullmatch(self.name):
            raise ToolRegistrationError("Invalid tool name.")
        normalized = " ".join(self.description.split()) if isinstance(self.description, str) else ""
        if not normalized or len(normalized) > 4096:
            raise ToolRegistrationError("Tool description must be non-empty and at most 4096 characters.")
        schema = freeze_json_object(self.input_schema)
        if schema.get("type") != "object":
            raise ToolRegistrationError("Tool input schema must have top-level type=object.")
        try:
            Draft202012Validator.check_schema(_plain(schema))
        except SchemaError as exc:
            raise ToolRegistrationError("Invalid Draft 2020-12 JSON schema.") from exc
        object.__setattr__(self, "description", normalized)
        object.__setattr__(self, "input_schema", schema)


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    session_id: str
    cancellation: CancellationToken

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id or not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("ToolContext requires run_id and session_id.")
        if not all(hasattr(self.cancellation, name) for name in ("cancelled", "raise_if_cancelled", "wait_cancelled")):
            raise TypeError("ToolContext requires a CancellationToken.")


@dataclass(frozen=True, slots=True)
class ToolTextContent:
    text: str
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.truncated, bool):
            raise TypeError("Invalid ToolTextContent.")


@dataclass(frozen=True, slots=True)
class ToolJsonContent:
    value: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", freeze_json(self.value))


ToolContent = ToolTextContent | ToolJsonContent


@dataclass(frozen=True, slots=True)
class ToolErrorInfo:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.code):
            raise ValueError("Invalid tool error code.")
        if not isinstance(self.message, str) or not isinstance(self.retryable, bool):
            raise TypeError("Invalid ToolErrorInfo.")
        normalized = self.message.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "".join(char for char in normalized if char in "\n\t" or ord(char) >= 32)
        normalized = " ".join(normalized.split())
        if not normalized:
            raise ValueError("Tool error message must be non-empty.")
        object.__setattr__(self, "message", normalized)


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    name: str
    content: ToolContent | None = None
    error: ToolErrorInfo | None = None

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("ToolExecutionResult must identify its ToolCall.")
        if self.content is not None and not isinstance(self.content, (ToolTextContent, ToolJsonContent)):
            raise TypeError("ToolExecutionResult content must be canonical ToolContent.")
        if self.error is not None and not isinstance(self.error, ToolErrorInfo):
            raise TypeError("ToolExecutionResult error must be ToolErrorInfo.")
        if bool(self.content is not None) == bool(self.error is not None):
            raise ValueError("ToolExecutionResult requires exactly one of content or error.")


@dataclass(frozen=True, slots=True)
class ToolEffectRecord:
    call_id: str
    tool_name: str
    effect_kind: ToolEffectKind
    status: ToolEffectStatus
    content: ToolContent | None = None
    error: ToolErrorInfo | None = None
    transcript_committed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not self.call_id or not isinstance(self.tool_name, str) or not self.tool_name:
            raise ValueError("ToolEffectRecord must identify its ToolCall.")
        if not isinstance(self.effect_kind, ToolEffectKind) or not isinstance(self.status, ToolEffectStatus):
            raise TypeError("ToolEffectRecord kind and status must be canonical enums.")
        if not isinstance(self.transcript_committed, bool):
            raise TypeError("transcript_committed must be bool.")
        if self.content is not None and not isinstance(self.content, (ToolTextContent, ToolJsonContent)):
            raise TypeError("ToolEffectRecord content must be canonical ToolContent.")
        if self.error is not None and not isinstance(self.error, ToolErrorInfo):
            raise TypeError("ToolEffectRecord error must be ToolErrorInfo.")
        if bool(self.content is not None) == bool(self.error is not None):
            raise ValueError("ToolEffectRecord requires exactly one of content or error.")
        if self.status is ToolEffectStatus.SUCCEEDED and self.content is None:
            raise ValueError("SUCCEEDED effect requires content.")
        if self.status is not ToolEffectStatus.SUCCEEDED and self.error is None:
            raise ValueError("Non-success effect requires error.")
        if self.status is ToolEffectStatus.TIMED_OUT and self.error and self.error.code != "timeout":
            raise ValueError("TIMED_OUT effect requires timeout error.")
        if self.status is ToolEffectStatus.CANCELLED and self.error and self.error.code != "cancelled":
            raise ValueError("CANCELLED effect requires cancelled error.")


@dataclass(frozen=True, slots=True)
class ToolBatchResult:
    calls: tuple[ToolCall, ...]
    results: tuple[ToolExecutionResult, ...]
    effects: tuple[ToolEffectRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", tuple(self.calls))
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "effects", tuple(self.effects))
        if len(self.calls) != len(self.results):
            raise ValueError("Every ToolCall must have one result.")
        if not all(isinstance(call, ToolCall) for call in self.calls):
            raise TypeError("ToolBatchResult.calls must contain ToolCall values.")
        if not all(isinstance(result, ToolExecutionResult) for result in self.results):
            raise TypeError("ToolBatchResult.results must contain canonical results.")
        if not all(isinstance(effect, ToolEffectRecord) for effect in self.effects):
            raise TypeError("ToolBatchResult.effects must contain canonical effects.")
        for call, result in zip(self.calls, self.results, strict=True):
            if (result.call_id, result.name) != (call.id, call.name):
                raise ValueError("Tool results must match ToolCalls in original order.")


class ToolBatchAborted(RuntimeError):
    def __init__(self, reason: RunError, effects: tuple[ToolEffectRecord, ...] = ()) -> None:
        if not isinstance(reason, RunError):
            raise TypeError("ToolBatchAborted requires RunError.")
        self.reason = reason
        self.effects = tuple(effects)
        if not all(isinstance(effect, ToolEffectRecord) for effect in self.effects):
            raise TypeError("ToolBatchAborted effects must be canonical.")
        super().__init__(reason.message)


ToolHandler = Callable[[FrozenJsonObject, ToolContext], ToolContent | Awaitable[ToolContent]]
TimeoutResolver = Callable[[FrozenJsonObject], float | None]


@dataclass(frozen=True, slots=True)
class Tool:
    definition: ToolDefinition
    handler: ToolHandler = field(repr=False)
    execution_mode: ToolExecutionMode = ToolExecutionMode.SERIAL
    effect_kind: ToolEffectKind = ToolEffectKind.READ_ONLY
    timeout: float | None = None
    timeout_resolver: TimeoutResolver | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ToolDefinition) or not callable(self.handler):
            raise TypeError("Tool requires a ToolDefinition and callable handler.")
        if not isinstance(self.execution_mode, ToolExecutionMode) or not isinstance(self.effect_kind, ToolEffectKind):
            raise TypeError("Tool execution_mode and effect_kind must be canonical enums.")
        if self.timeout is not None and (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or self.timeout <= 0):
            raise ValueError("Tool timeout must be positive.")

    async def execute(self, arguments: FrozenJsonObject, context: ToolContext) -> ToolContent:
        value = self.handler(arguments, context)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, (ToolTextContent, ToolJsonContent)):
            raise ToolContractError("Tool must return canonical ToolContent.")
        return value

    def validate(self, arguments: FrozenJsonObject) -> ToolErrorInfo | None:
        return validate_tool_arguments(self, arguments)

    def requested_timeout(self, arguments: FrozenJsonObject) -> float | None:
        return self.timeout_resolver(arguments) if self.timeout_resolver else None


class ToolExecutionPolicy(Protocol):
    async def evaluate(self, call: ToolCall, tool: Tool | None, context: ToolContext) -> ToolDecision: ...


class AllowAllToolPolicy:
    async def evaluate(self, call: ToolCall, tool: Tool | None, context: ToolContext) -> ToolDecision:
        return ToolDecision.ALLOW


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        self._sealed = False
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if self._sealed:
            raise ToolRegistrationError("ToolRegistry snapshot is immutable.")
        if not isinstance(replace, bool):
            raise ToolRegistrationError("replace must be bool.")
        if not isinstance(getattr(tool, "definition", None), ToolDefinition):
            raise ToolRegistrationError("Tool must expose a canonical ToolDefinition.")
        if getattr(tool, "execution_mode", None) not in set(ToolExecutionMode):
            raise ToolRegistrationError("Tool must declare a canonical execution mode.")
        if getattr(tool, "effect_kind", None) not in set(ToolEffectKind):
            raise ToolRegistrationError("Tool must declare a canonical effect kind.")
        if not inspect.iscoroutinefunction(getattr(tool, "execute", None)):
            raise ToolRegistrationError("Tool must expose async execute().")
        timeout = getattr(tool, "timeout", None)
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ToolRegistrationError("Tool timeout must be positive or None.")
        name = tool.definition.name
        if name in self._tools and not replace:
            raise ToolRegistrationError(f"Tool {name!r} is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    def snapshot(self) -> "ToolRegistry":
        return ToolRegistry(tuple(self._tools.values()))

    def _seal(self) -> "ToolRegistry":
        self._sealed = True
        return self


def validate_tool_arguments(tool: object, arguments: FrozenJsonObject) -> ToolErrorInfo | None:
    definition = getattr(tool, "definition")
    assert isinstance(definition, ToolDefinition)
    try:
        Draft202012Validator(_plain(definition.input_schema)).validate(_plain(arguments))
    except ValidationError:
        return ToolErrorInfo("invalid_arguments", "Tool arguments do not match the input schema.")
    return None


def _plain(value: object) -> object:
    from roboagent.message import thaw_json

    return thaw_json(freeze_json(value))
