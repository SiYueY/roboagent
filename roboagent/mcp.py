"""MCP discovery and adaptation into the canonical Tool pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, TypeAlias

from roboagent.message import FrozenJsonObject, JsonValue, freeze_json, freeze_json_object
from roboagent.runtime import CancellationToken
from roboagent.tool import (
    BinaryToolContent,
    RawToolContent,
    RawToolResult,
    ResourceToolContent,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectUnknown,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolExecutionMode,
    ToolJsonContent,
    ToolTextContent,
)


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: FrozenJsonObject
    effect_kind: ToolEffectKind | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", freeze_json_object(self.input_schema))
        if self.effect_kind is not None and not isinstance(self.effect_kind, ToolEffectKind):
            raise TypeError("MCP effect metadata must be ToolEffectKind or None.")


@dataclass(frozen=True, slots=True)
class MCPTextContent:
    text: str


@dataclass(frozen=True, slots=True)
class MCPJsonContent:
    value: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", freeze_json(self.value))


@dataclass(frozen=True, slots=True)
class MCPBinaryContent:
    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class MCPResourceContent:
    uri: str
    data: bytes | None = None
    media_type: str | None = None


MCPContent: TypeAlias = MCPTextContent | MCPJsonContent | MCPBinaryContent | MCPResourceContent


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    content: tuple[MCPContent, ...] = ()
    is_error: bool = False
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", tuple(self.content))
        if not all(isinstance(item, (MCPTextContent, MCPJsonContent, MCPBinaryContent, MCPResourceContent)) for item in self.content):
            raise TypeError("Unknown MCP content block.")
        if not isinstance(self.is_error, bool):
            raise TypeError("is_error must be bool.")


class MCPClient(Protocol):
    async def connect(self) -> None: ...
    async def list_tools(self) -> Sequence[MCPToolDefinition]: ...
    async def call_tool(self, name: str, arguments: FrozenJsonObject, cancellation: CancellationToken) -> MCPToolResult: ...
    async def close(self) -> None: ...


class MCPError(RuntimeError):
    code = "mcp_protocol_error"


class MCPConnectionError(MCPError):
    code = "mcp_connection_error"


class MCPProtocolError(MCPError):
    code = "mcp_protocol_error"


@dataclass(frozen=True, slots=True)
class MCPToolConfig:
    effect_kind: ToolEffectKind | None = None
    execution_mode: ToolExecutionMode = ToolExecutionMode.SERIAL
    error_proves_not_executed: bool = False

    def __post_init__(self) -> None:
        if self.effect_kind is not None and not isinstance(self.effect_kind, ToolEffectKind):
            raise TypeError("Local MCP effect kind must be canonical.")
        if not isinstance(self.execution_mode, ToolExecutionMode) or not isinstance(self.error_proves_not_executed, bool):
            raise TypeError("Invalid local MCP Tool configuration.")


class MCPToolPolicy:
    """Immutable trusted-local MCP Tool classification."""

    def __init__(self, tools: Mapping[str, MCPToolConfig] | None = None) -> None:
        values = dict(tools or {})
        if not all(isinstance(name, str) and name and isinstance(config, MCPToolConfig) for name, config in values.items()):
            raise TypeError("MCPToolPolicy requires named MCPToolConfig values.")
        self._tools = MappingProxyType(values)

    def resolve(self, name: str) -> MCPToolConfig:
        return self._tools.get(name, MCPToolConfig())


class MCPEventEmitter(Protocol):
    async def emit(self, event_type: str, **payload: object) -> object: ...


class MCPToolAdapter:
    def __init__(
        self,
        client: MCPClient,
        local: Mapping[str, MCPToolConfig] | None = None,
        *,
        policy: MCPToolPolicy | None = None,
    ) -> None:
        if local is not None and policy is not None:
            raise ValueError("Pass either local MCP configuration or MCPToolPolicy, not both.")
        self.client = client
        self.policy = policy or MCPToolPolicy(local)

    def adapt(self, definition: MCPToolDefinition) -> Tool:
        if not isinstance(definition, MCPToolDefinition):
            raise TypeError("definition must be MCPToolDefinition.")
        config = self.policy.resolve(definition.name)
        effect = _effect_kind(config.effect_kind, definition.effect_kind)

        async def invoke(arguments: FrozenJsonObject, context: ToolContext) -> RawToolResult:
            try:
                result = await self.client.call_tool(definition.name, arguments, context.cancellation)
            except (ToolExecutionFailure, ToolEffectUnknown):
                raise
            except Exception as exc:
                code = "mcp_connection_error" if isinstance(exc, (ConnectionError, TimeoutError, OSError)) else "mcp_protocol_error"
                error = ToolErrorInfo(code, "MCP invocation failed.")
                if effect is ToolEffectKind.SIDE_EFFECTING:
                    raise ToolEffectUnknown(error) from exc
                raise ToolExecutionFailure(error) from exc
            if not isinstance(result, MCPToolResult):
                error = ToolErrorInfo("mcp_protocol_error", "MCP client returned a non-canonical result.")
                if effect is ToolEffectKind.SIDE_EFFECTING:
                    raise ToolEffectUnknown(error)
                raise ToolExecutionFailure(error)
            if result.is_error:
                error = ToolErrorInfo("mcp_tool_error", result.error_message or "MCP tool returned an error.")
                if effect is ToolEffectKind.SIDE_EFFECTING and not config.error_proves_not_executed:
                    raise ToolEffectUnknown(error)
                raise ToolExecutionFailure(error)
            try:
                return RawToolResult(tuple(_map_content(item) for item in result.content))
            except Exception as exc:
                error = ToolErrorInfo("mcp_protocol_error", "MCP result contained an unknown content block.")
                if effect is ToolEffectKind.SIDE_EFFECTING:
                    raise ToolEffectUnknown(error) from exc
                raise ToolExecutionFailure(error) from exc

        return Tool(
            ToolDefinition(definition.name, definition.description, definition.input_schema),
            invoke,
            config.execution_mode,
            effect,
        )

    async def discover(self) -> tuple[Tool, ...]:
        try:
            definitions = await self.client.list_tools()
        except Exception as exc:
            raise MCPConnectionError("MCP discovery failed.") from exc
        if not all(isinstance(item, MCPToolDefinition) for item in definitions):
            raise MCPProtocolError("MCP discovery returned an unknown definition.")
        return tuple(self.adapt(item) for item in definitions)


class MCPServer:
    """Small application-owned connection lifecycle helper."""

    def __init__(
        self,
        client: MCPClient,
        *,
        local: Mapping[str, MCPToolConfig] | None = None,
        policy: MCPToolPolicy | None = None,
        events: MCPEventEmitter | None = None,
    ) -> None:
        self.client = client
        self.adapter = MCPToolAdapter(client, local, policy=policy)
        self.events = events
        self._connected = False

    async def __aenter__(self) -> "MCPServer":
        if self._connected:
            raise RuntimeError("MCPServer is already connected.")
        try:
            await self.client.connect()
        except Exception as exc:
            raise MCPConnectionError("MCP connection failed.") from exc
        self._connected = True
        await self._emit("mcp.connected")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        error: MCPConnectionError | None = None
        cause: Exception | None = None
        try:
            await self.client.close()
        except Exception as exc:
            error = MCPConnectionError("MCP disconnect failed.")
            cause = exc
        finally:
            self._connected = False
            await self._emit(
                "mcp.disconnected", error_code=None if error is None else error.code
            )
        if error is not None:
            raise error from cause

    async def tools(self) -> tuple[Tool, ...]:
        if not self._connected:
            raise RuntimeError("MCP tools may only be discovered while connected.")
        return await self.adapter.discover()

    async def _emit(self, event_type: str, **payload: object) -> None:
        if self.events is not None:
            await self.events.emit(event_type, **payload)


def _effect_kind(local: ToolEffectKind | None, remote: ToolEffectKind | None) -> ToolEffectKind:
    # Remote declarations may upgrade risk. Only explicit trusted local config may start at READ_ONLY.
    base = local or ToolEffectKind.SIDE_EFFECTING
    if remote is ToolEffectKind.SIDE_EFFECTING:
        return ToolEffectKind.SIDE_EFFECTING
    return base


def _map_content(content: MCPContent) -> RawToolContent:
    if isinstance(content, MCPTextContent):
        return ToolTextContent(content.text)
    if isinstance(content, MCPJsonContent):
        return ToolJsonContent(content.value)
    if isinstance(content, MCPBinaryContent):
        return BinaryToolContent(content.data, content.media_type)
    if isinstance(content, MCPResourceContent):
        return ResourceToolContent(content.uri, content.data, content.media_type)
    raise TypeError("Unknown MCP content block.")
