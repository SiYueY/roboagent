from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent
from roboagent.mcp import (
    MCPBinaryContent,
    MCPJsonContent,
    MCPResourceContent,
    MCPServer,
    MCPTextContent,
    MCPToolAdapter,
    MCPToolConfig,
    MCPToolDefinition,
    MCPToolResult,
)
from roboagent.message import FrozenJsonObject, ToolCall
from roboagent.model import ModelCapabilities
from roboagent.runtime import RuntimeCancellation
from roboagent.tool import (
    InMemoryWorkspace,
    ToolContext,
    ToolEffectKind,
    ToolEffectStatus,
    ToolExecutor,
    ToolOutputLimits,
    ToolRegistrationError,
    ToolRegistry,
    WorkspaceToolResultMaterializer,
)


class _Client:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.lifecycle = []

    async def connect(self):
        self.lifecycle.append("connect")

    async def list_tools(self):
        self.lifecycle.append("discover")
        return (MCPToolDefinition("remote", "Remote tool.", FrozenJsonObject({"type": "object"})),)

    async def call_tool(self, name, arguments, cancellation):
        if self.error:
            raise self.error
        return self.result

    async def close(self):
        self.lifecycle.append("close")


class _UnusedModel:
    capabilities = ModelCapabilities(tool_calling=True)

    async def stream(self, context, settings=None):
        raise AssertionError
        yield


def _context():
    return ToolContext("run", "session", RuntimeCancellation())


def test_mcp_lifecycle_discovery_precedes_agent_registry_seal() -> None:
    async def check() -> None:
        client = _Client(MCPToolResult())
        async with MCPServer(client) as server:
            tools = await server.tools()
            registry = ToolRegistry(tools)
            agent = Agent(_UnusedModel(), tool_registry=registry)
            with pytest.raises(ToolRegistrationError):
                agent.tool_registry.register(tools[0])
        assert client.lifecycle == ["connect", "discover", "close"]

    asyncio.run(check())


def test_mcp_multi_content_maps_in_order_and_uses_normal_materializer() -> None:
    async def check() -> None:
        client = _Client(
            MCPToolResult(
                (
                    MCPTextContent("one"),
                    MCPBinaryContent(b"image", "image/png"),
                    MCPJsonContent({"three": 3}),
                    MCPResourceContent("mcp://resource", b"four", "text/plain"),
                )
            )
        )
        tool = MCPToolAdapter(client).adapt((await client.list_tools())[0])
        workspace = InMemoryWorkspace()
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace, limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4)
        )
        batch = await ToolExecutor(
            registry=ToolRegistry((tool,)), result_materializer=materializer
        ).execute((ToolCall("call", "remote"),), _context())
        content = batch.results[0].content
        assert content is not None
        assert [item.media_type for item in content] == ["text/plain", "image/png", "application/json", "text/plain"]

    asyncio.run(check())


@pytest.mark.parametrize(
    ("local", "remote", "expected"),
    [
        (None, ToolEffectKind.READ_ONLY, ToolEffectKind.SIDE_EFFECTING),
        (ToolEffectKind.READ_ONLY, ToolEffectKind.SIDE_EFFECTING, ToolEffectKind.SIDE_EFFECTING),
        (ToolEffectKind.SIDE_EFFECTING, ToolEffectKind.READ_ONLY, ToolEffectKind.SIDE_EFFECTING),
        (ToolEffectKind.READ_ONLY, ToolEffectKind.READ_ONLY, ToolEffectKind.READ_ONLY),
    ],
)
def test_remote_metadata_cannot_lower_effect_risk(local, remote, expected) -> None:
    definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}), remote)
    config = {} if local is None else {"remote": MCPToolConfig(effect_kind=local)}
    assert MCPToolAdapter(_Client(), config).adapt(definition).effect_kind is expected


@pytest.mark.parametrize(
    ("effect_kind", "status"),
    [
        (ToolEffectKind.READ_ONLY, ToolEffectStatus.FAILED),
        (ToolEffectKind.SIDE_EFFECTING, ToolEffectStatus.UNKNOWN),
    ],
)
def test_mcp_error_and_disconnect_use_effect_trust(effect_kind, status) -> None:
    async def check(result=None, error=None):
        client = _Client(result, error)
        definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}))
        tool = MCPToolAdapter(client, {"remote": MCPToolConfig(effect_kind=effect_kind)}).adapt(definition)
        batch = await ToolExecutor(registry=ToolRegistry((tool,))).execute((ToolCall("call", "remote"),), _context())
        assert batch.effects[0].status is status

    asyncio.run(check(MCPToolResult(is_error=True)))
    asyncio.run(check(error=ConnectionError("disconnect")))
