from __future__ import annotations

import asyncio

import pytest

from roboagent import Agent
from roboagent.agent import HookDecision
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
    MCPToolPolicy,
)
from roboagent.message import FrozenJsonObject, ToolCall
from roboagent.model import ModelCapabilities
from roboagent.runtime import RuntimeCancellation
from roboagent.runtime.event import RunEventEmitter
from roboagent.tool import (
    ApprovalDecision,
    ApprovalResponse,
    InMemoryWorkspace,
    ToolContext,
    ToolDecision,
    ToolEffectKind,
    ToolEffectStatus,
    ToolExecutor,
    ToolOutputLimits,
    ToolPolicyDecision,
    ToolRegistrationError,
    ToolRegistry,
    ToolTextContent,
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


def test_mcp_discovery_requires_active_connection() -> None:
    async def check() -> None:
        server = MCPServer(_Client(MCPToolResult()))
        with pytest.raises(RuntimeError, match="while connected"):
            await server.tools()
        async with server:
            assert len(await server.tools()) == 1
        with pytest.raises(RuntimeError, match="while connected"):
            await server.tools()

    asyncio.run(check())


def test_mcp_lifecycle_events_contain_no_client_state() -> None:
    async def check() -> None:
        events = RunEventEmitter("mcp-lifecycle")
        client = _Client(MCPToolResult())
        client.password = "do-not-publish"
        async with MCPServer(client, events=events):
            pass
        assert [event.type for event in events.history] == ["mcp.connected", "mcp.disconnected"]
        assert "do-not-publish" not in repr(tuple(event.payload for event in events.history))

    asyncio.run(check())


def test_mcp_tool_uses_the_normal_approval_pipeline() -> None:
    async def check() -> None:
        client = _Client(MCPToolResult((MCPTextContent("approved"),)))
        tool = MCPToolAdapter(client).adapt((await client.list_tools())[0])

        class Policy:
            async def evaluate(self, call, selected, context):
                return ToolPolicyDecision(ToolDecision.REQUIRE_APPROVAL)

        class Approver:
            async def request(self, request, cancellation):
                return ApprovalResponse(
                    request.approval_id, request.arguments_digest, ApprovalDecision.APPROVE
                )

        batch = await ToolExecutor(
            registry=ToolRegistry((tool,)), policy=Policy(), approval_provider=Approver()
        ).execute((ToolCall("call", "remote"),), _context())
        assert batch.results[0].content == (ToolTextContent("approved"),)

    asyncio.run(check())


def test_mcp_tool_uses_normal_hooks() -> None:
    async def check() -> None:
        observed = []

        class Hook:
            async def before_tool(self, context, call):
                observed.append(("before", call.name))
                return HookDecision.CONTINUE

            async def after_tool(self, context, result):
                observed.append(("after", result.name))

        client = _Client(MCPToolResult((MCPTextContent("done"),)))
        tool = MCPToolAdapter(client).adapt((await client.list_tools())[0])
        await ToolExecutor(registry=ToolRegistry((tool,)), hooks=(Hook(),)).execute(
            (ToolCall("call", "remote"),), _context()
        )
        assert observed == [("before", "remote"), ("after", "remote")]

    asyncio.run(check())


def test_mcp_tool_policy_is_immutable_trusted_local_configuration() -> None:
    policy = MCPToolPolicy({"remote": MCPToolConfig(effect_kind=ToolEffectKind.READ_ONLY)})
    definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}))
    assert MCPToolAdapter(_Client(), policy=policy).adapt(definition).effect_kind is ToolEffectKind.READ_ONLY
    with pytest.raises(TypeError):
        policy._tools["remote"] = MCPToolConfig()  # type: ignore[index]


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


def test_mcp_connection_and_protocol_errors_use_stable_taxonomy() -> None:
    async def check(result, expected_code):
        client = _Client(result)
        definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}))
        tool = MCPToolAdapter(
            client, {"remote": MCPToolConfig(effect_kind=ToolEffectKind.READ_ONLY)}
        ).adapt(definition)
        batch = await ToolExecutor(registry=ToolRegistry((tool,))).execute(
            (ToolCall("call", "remote"),), _context()
        )
        assert batch.results[0].error.code == expected_code
        assert batch.effects[0].error.code == expected_code

    asyncio.run(check(object(), "mcp_protocol_error"))

    async def disconnected() -> None:
        client = _Client(error=ConnectionError("closed"))
        definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}))
        tool = MCPToolAdapter(
            client, {"remote": MCPToolConfig(effect_kind=ToolEffectKind.READ_ONLY)}
        ).adapt(definition)
        batch = await ToolExecutor(registry=ToolRegistry((tool,))).execute(
            (ToolCall("call", "remote"),), _context()
        )
        assert batch.results[0].error.code == "mcp_connection_error"

    asyncio.run(disconnected())


def test_mcp_unknown_content_block_fails_explicitly() -> None:
    async def check() -> None:
        malformed = object.__new__(MCPToolResult)
        object.__setattr__(malformed, "content", (object(),))
        object.__setattr__(malformed, "is_error", False)
        object.__setattr__(malformed, "error_message", None)
        client = _Client(malformed)
        definition = MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"}))
        tool = MCPToolAdapter(
            client, {"remote": MCPToolConfig(effect_kind=ToolEffectKind.READ_ONLY)}
        ).adapt(definition)
        batch = await ToolExecutor(registry=ToolRegistry((tool,))).execute(
            (ToolCall("call", "remote"),), _context()
        )
        assert batch.results[0].error.code == "mcp_protocol_error"
        assert batch.effects[0].status is ToolEffectStatus.FAILED

    asyncio.run(check())
