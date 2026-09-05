from __future__ import annotations

import asyncio

import pytest

from roboagent.message import ArtifactReferenceContent, FrozenJsonObject, JsonContent, TextContent, ToolCall
from roboagent.runtime import RuntimeCancellation
from roboagent.tool import (
    BinaryToolContent,
    InMemoryWorkspace,
    LocalWorkspace,
    RawToolResult,
    ResourceToolContent,
    Tool,
    ToolBatchAborted,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectStatus,
    ToolExecutionResult,
    ToolExecutor,
    ToolJsonContent,
    ToolOutputLimits,
    ToolRegistry,
    ToolTextContent,
    WorkspacePermissionError,
    WorkspaceError,
    WorkspaceArtifactMissingError,
    WorkspaceToolResultMaterializer,
    read_artifact,
    result_message,
    retry_safe,
)


def _call() -> ToolCall:
    return ToolCall("call", "work", FrozenJsonObject())


def _context() -> ToolContext:
    return ToolContext("run", "session", RuntimeCancellation())


def _definition() -> ToolDefinition:
    return ToolDefinition("work", "Do work.", FrozenJsonObject({"type": "object"}))


def test_raw_multi_content_order_survives_inline_materialization() -> None:
    async def check() -> None:
        raw = RawToolResult((ToolTextContent("one"), ToolJsonContent({"two": 2}), ToolTextContent("three")))
        tool = Tool(_definition(), lambda arguments, context: raw)
        batch = await ToolExecutor(registry=ToolRegistry((tool,))).execute((_call(),), _context())
        assert batch.results[0].content == (ToolTextContent("one"), ToolJsonContent({"two": 2}), ToolTextContent("three"))
        message = result_message(batch.results[0])
        assert message.content == (TextContent("one"), JsonContent({"two": 2}), TextContent("three"))

    asyncio.run(check())


def test_transport_scoped_resource_without_bytes_fails_explicitly() -> None:
    async def check() -> None:
        raw = RawToolResult((ResourceToolContent("mcp://temporary/resource"),))
        tool = Tool(_definition(), lambda arguments, context: raw)
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry((tool,))).execute((_call(),), _context())
        assert caught.value.reason.code == "workspace_artifact_missing"
        assert caught.value.effects[0].status is ToolEffectStatus.SUCCEEDED

    asyncio.run(check())


def test_workspace_metadata_mismatch_fails_after_preserving_physical_success() -> None:
    async def check() -> None:
        class InconsistentWorkspace(InMemoryWorkspace):
            async def write(self, path, data, *, media_type=None):
                entry = await super().write(path, data, media_type=media_type)
                return type(entry)(entry.path, entry.size + 1, entry.media_type, entry.digest)

        workspace = InconsistentWorkspace()
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=100, max_inline_bytes=1),
        )
        tool = Tool(_definition(), lambda arguments, context: ToolTextContent("large"))
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(
                registry=ToolRegistry((tool,)), result_materializer=materializer
            ).execute((_call(),), _context())
        assert caught.value.reason.code == "tool_materialization_error"
        assert caught.value.effects[0].status is ToolEffectStatus.SUCCEEDED

    asyncio.run(check())


def test_large_and_binary_blocks_materialize_once_in_original_order() -> None:
    async def check() -> None:
        class CountingWorkspace(InMemoryWorkspace):
            writes = 0

            async def write(self, path, data, *, media_type=None):
                self.writes += 1
                return await super().write(path, data, media_type=media_type)

        workspace = CountingWorkspace()
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4),
        )
        raw = RawToolResult((ToolTextContent("large text"), BinaryToolContent(b"png", "image/png")))
        content = await materializer.materialize(raw, call=_call(), context=_context(), cancellation=_context().cancellation)
        assert workspace.writes == 2
        assert len(content) == 2 and all(isinstance(item, ArtifactReferenceContent) for item in content)
        assert content[0].preview == "large text" and content[1].media_type == "image/png"

    asyncio.run(check())


def test_materialization_failure_preserves_succeeded_physical_effect() -> None:
    async def check() -> None:
        class BrokenWorkspace(InMemoryWorkspace):
            async def write(self, path, data, *, media_type=None):
                raise OSError("disk full")

        raw = RawToolResult((ToolTextContent("large result"),))
        tool = Tool(_definition(), lambda arguments, context: raw, effect_kind=ToolEffectKind.SIDE_EFFECTING)
        materializer = WorkspaceToolResultMaterializer(
            workspace=BrokenWorkspace(),
            limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=2),
        )
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry((tool,)), result_materializer=materializer).execute(
                (_call(),), _context()
            )
        effect = caught.value.effects[0]
        assert caught.value.reason.code == "tool_materialization_error"
        assert effect.status is ToolEffectStatus.SUCCEEDED
        assert not effect.transcript_committed
        assert not retry_safe(caught.value.effects)
        assert isinstance(effect.content, ToolJsonContent)
        assert len(str(effect.content.value)) < 2048

    asyncio.run(check())


def test_output_over_raw_limit_has_same_effect_truth() -> None:
    async def check() -> None:
        workspace = InMemoryWorkspace()
        raw = RawToolResult((ToolTextContent("too large"),))
        tool = Tool(_definition(), lambda arguments, context: raw)
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=3, max_inline_bytes=2),
        )
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry((tool,)), result_materializer=materializer).execute(
                (_call(),), _context()
            )
        assert caught.value.reason.code == "tool_output_too_large"
        assert caught.value.effects[0].status is ToolEffectStatus.SUCCEEDED

    asyncio.run(check())


def test_artifact_reference_can_enter_tool_result_message() -> None:
    artifact = ArtifactReferenceContent(
        "workspace://blobs/sha256/" + "a" * 64,
        "text/plain",
        5,
        "sha256:" + "a" * 64,
        "hello",
    )
    message = result_message(ToolExecutionResult("call", "work", content=(artifact,)))
    assert message.content == (artifact,)


def test_local_workspace_rejects_escape_absolute_and_symlink(tmp_path) -> None:
    async def check() -> None:
        workspace = LocalWorkspace(tmp_path / "workspace")
        await workspace.write("files/ok", b"ok")
        assert await workspace.read("files/ok") == b"ok"
        for path in ("../escape", "/absolute"):
            with pytest.raises(WorkspacePermissionError):
                await workspace.write(path, b"bad")
        outside = tmp_path / "outside"
        outside.mkdir()
        (workspace.root / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(WorkspacePermissionError):
            await workspace.write("link/escape", b"bad")

    asyncio.run(check())


def test_local_artifact_is_durable_immutable_and_integrity_checked(tmp_path) -> None:
    async def check() -> None:
        root = tmp_path / "workspace"
        workspace = LocalWorkspace(root)
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=100, max_inline_bytes=1),
        )
        raw = RawToolResult((ToolTextContent("persistent"),))
        first = await materializer.materialize(
            raw, call=_call(), context=_context(), cancellation=_context().cancellation
        )
        second = await materializer.materialize(
            raw, call=_call(), context=_context(), cancellation=_context().cancellation
        )
        assert first == second
        artifact = first[0]
        assert isinstance(artifact, ArtifactReferenceContent)

        reopened = LocalWorkspace(root)
        assert await read_artifact(reopened, artifact) == b"persistent"
        with pytest.raises(WorkspacePermissionError):
            await reopened.delete(artifact.uri.removeprefix("workspace://"))

        wrong_digest = ArtifactReferenceContent(
            artifact.uri, artifact.media_type, artifact.size, "sha256:" + "0" * 64
        )
        with pytest.raises(WorkspaceError, match="integrity"):
            await read_artifact(reopened, wrong_digest)

        missing = ArtifactReferenceContent(
            "workspace://blobs/sha256/" + "f" * 64,
            "text/plain",
            1,
            "sha256:" + "f" * 64,
        )
        with pytest.raises(WorkspaceArtifactMissingError) as caught:
            await read_artifact(reopened, missing)
        assert caught.value.code == "workspace_artifact_missing"

    asyncio.run(check())
