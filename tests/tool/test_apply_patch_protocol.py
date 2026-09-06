from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

import pytest

from roboagent.message import ToolCall
from roboagent.runtime import (
    ExecutionBudgetConfig,
    ExecutionRecordType,
    ExecutionTree,
    RuntimeCancellation,
    RuntimeToolExecutionContext,
)
from roboagent.tool import (
    ApplyPatchConfig,
    FilesystemConfig,
    FilesystemWorkspace,
    ToolContext,
    ToolBatchCancelled,
    ToolEffectStatus,
    ToolExecutor,
    ToolRegistry,
    create_apply_patch_tool,
    retry_safe,
)

patch_module = importlib.import_module("roboagent.tool.apply_patch")


def _runtime(root: Path, *, settlement_timeout: float = 1, **limits):
    tool = create_apply_patch_tool(
        ApplyPatchConfig(FilesystemConfig(FilesystemWorkspace(root)), **limits)
    )
    tree = ExecutionTree(
        root_run_id="run",
        cancellation=RuntimeCancellation(),
        deadline=None,
        budget=ExecutionBudgetConfig(),
        settlement_timeout=settlement_timeout,
        cleanup_timeout=1,
        max_execution_records=64,
        max_record_evidence_bytes=4096,
    )
    executor = ToolExecutor(registry=ToolRegistry((tool,)))
    context = ToolContext(
        "run",
        "session",
        tree.cancellation,
        RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
    )
    return tree, executor, context


async def _apply(executor, context, patch: str):
    return await executor.execute(
        (ToolCall("call", "apply_patch", {"patch": patch}),), context
    )


def _one(operation: str, path: str, body: str = "") -> str:
    return f"*** Begin Patch\n*** {operation} File: {path}\n{body}*** End Patch"


@pytest.mark.parametrize(
    "path",
    [
        "/absolute",
        ".",
        "a/./b",
        "a/../b",
        "a/",
        "a//b",
        " a",
        "\ta",
        "a ",
        "a\t",
    ],
)
def test_apply_patch_rejects_every_noncanonical_path(tmp_path, path) -> None:
    async def check() -> None:
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one("Add", path, "+x\n"))
        assert result.results[0].error.code == "invalid_path"
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_encoding_binary_limits_and_body_grammar(tmp_path) -> None:
    async def check() -> None:
        cases = (
            (_one("Add", "x", "plain\n"), "invalid_patch"),
            (
                _one("Add", "x", "+a\n\\ No newline at end of file\n+b\n"),
                "invalid_patch",
            ),
            (_one("Delete", "x", "+body\n"), "invalid_patch"),
            (_one("Update", "x", "@@ -1 +1 @@\n-x\n+y\n"), "invalid_patch"),
            (_one("Add", "\ud800", "+x\n"), "unsupported_binary_content"),
        )
        for patch, code in cases:
            tree, executor, context = _runtime(tmp_path)
            result = await _apply(executor, context, patch)
            assert result.results[0].error.code == code
            assert tree.effects == ()

        (tmp_path / "binary").write_bytes(b"a\x00b")
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one("Delete", "binary"))
        assert result.results[0].error.code == "unsupported_binary_content"
        assert (tmp_path / "binary").read_bytes() == b"a\x00b"
        assert tree.effects == ()

        (tmp_path / "bom").write_bytes(b"\xef\xbb\xbftext")
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one("Delete", "bom"))
        assert result.results[0].error.code == "unsupported_binary_content"

        (tmp_path / "invalid-utf8").write_bytes(b"\xff")
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one("Delete", "invalid-utf8"))
        assert result.results[0].error.code == "unsupported_binary_content"

        tree, executor, context = _runtime(tmp_path, max_patch_bytes=8)
        result = await _apply(executor, context, _one("Add", "x", "+x\n"))
        assert result.results[0].error.code == "patch_too_large"

        (tmp_path / "large").write_text("12345")
        tree, executor, context = _runtime(tmp_path, max_file_bytes=4)
        result = await _apply(executor, context, _one("Delete", "large"))
        assert result.results[0].error.code == "patch_file_too_large"

    asyncio.run(check())


def test_apply_patch_add_uses_process_default_creation_mode(tmp_path) -> None:
    async def check() -> None:
        probe = tmp_path / "mode-probe"
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(descriptor)
        expected_mode = probe.stat().st_mode & 0o777
        probe.unlink()
        _tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one("Add", "file", "+value\n"))
        assert result.results[0].error is None
        assert (tmp_path / "file").stat().st_mode & 0o777 == expected_mode

    asyncio.run(check())


@pytest.mark.parametrize(
    ("before", "expected_ending"),
    [
        (b"a\nb\nc\r\n", b"new\n"),
        (b"a\r\nb\r\nc\n", b"new\r\n"),
        (b"a\r\nb\n", b"new\r\n"),
        (b"a\nb\r\n", b"new\n"),
        (b"a", b"new\n"),
    ],
)
def test_apply_patch_dominant_newline_rules(tmp_path, before, expected_ending) -> None:
    async def check() -> None:
        target = tmp_path / "file"
        target.write_bytes(before)
        first = before.splitlines()[0].decode()
        marker = "\\ No newline at end of file\n" if b"\n" not in before else ""
        patch = _one("Update", "file", f"@@\n-{first}\n{marker}+new\n")
        _tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, patch)
        assert result.results[0].error is None
        assert target.read_bytes().startswith(expected_ending)

    asyncio.run(check())


def test_apply_patch_multi_hunk_is_sequential_and_preserves_mode(tmp_path) -> None:
    async def check() -> None:
        target = tmp_path / "file"
        target.write_text("a\nb\nc\n")
        os.chmod(target, 0o751)
        patch = _one(
            "Update",
            "file",
            "@@\n-a\n+x\n@@   \n x\n-b\n+y\n",
        )
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, patch)
        assert result.results[0].error is None
        assert target.read_text() == "x\ny\nc\n"
        assert target.stat().st_mode & 0o777 == 0o751
        assert len(tree.effects) == 1

    asyncio.run(check())


@pytest.mark.parametrize("change", ["update", "delete", "add", "chmod"])
def test_apply_patch_revalidates_after_staging(tmp_path, monkeypatch, change) -> None:
    async def check() -> None:
        nested = tmp_path / "dir"
        nested.mkdir()
        target = nested / "file"
        if change != "add":
            target.write_text("old\n")
        operation = (
            "Add" if change == "add" else "Delete" if change == "delete" else "Update"
        )
        body = (
            "+new\n"
            if change == "add"
            else ""
            if change == "delete"
            else "@@\n-old\n+new\n"
        )
        original_stage = patch_module._stage

        def stage(plans):
            original_stage(plans)
            if change == "update":
                target.write_text("changed\n")
            elif change == "delete":
                target.write_text("changed\n")
            elif change == "add":
                target.write_text("concurrent\n")
            else:
                os.chmod(target, 0o600)

        monkeypatch.setattr(patch_module, "_stage", stage)
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(executor, context, _one(operation, "dir/file", body))
        assert result.results[0].error.code == "patch_target_changed"
        assert not tuple(nested.glob("*.patch"))
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_revalidates_parent_symlink_and_hardlink_alias(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        nested = tmp_path / "dir"
        nested.mkdir()
        (nested / "file").write_text("old\n")
        original_stage = patch_module._stage

        def stage(plans):
            original_stage(plans)
            for plan in plans:
                if plan.staged is not None:
                    plan.staged.unlink()
            (nested / "file").unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)

        monkeypatch.setattr(patch_module, "_stage", stage)
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(
            executor,
            context,
            _one("Update", "dir/file", "@@\n-old\n+new\n"),
        )
        assert result.results[0].error.code == "patch_target_changed"
        assert not (outside / "file").exists()
        assert tree.effects == ()

        first = tmp_path / "first"
        first.write_text("same\n")
        os.link(first, tmp_path / "second")
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Delete File: first
*** Delete File: second
*** End Patch"""
        result = await _apply(executor, context, patch)
        assert result.results[0].error.code == "duplicate_patch_target"
        assert first.exists() and (tmp_path / "second").exists()

    asyncio.run(check())


def test_apply_patch_revalidates_removed_parent_after_staging(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        nested = tmp_path / "dir"
        nested.mkdir()
        target = nested / "file"
        target.write_text("old\n")
        original_stage = patch_module._stage

        def stage(plans):
            original_stage(plans)
            for plan in plans:
                if plan.staged is not None:
                    plan.staged.unlink()
            target.unlink()
            nested.rmdir()

        monkeypatch.setattr(patch_module, "_stage", stage)
        tree, executor, context = _runtime(tmp_path)
        result = await _apply(
            executor,
            context,
            _one("Update", "dir/file", "@@\n-old\n+new\n"),
        )
        assert result.results[0].error.code == "patch_target_changed"
        assert not nested.exists()
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_rejects_platform_casefold_alias(tmp_path, monkeypatch) -> None:
    async def check() -> None:
        monkeypatch.setattr(
            patch_module.os.path, "normcase", lambda value: value.casefold()
        )
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Add File: Name
+one
*** Add File: name
+two
*** End Patch"""
        result = await _apply(executor, context, patch)
        assert result.results[0].error.code == "duplicate_patch_target"
        assert not (tmp_path / "Name").exists()
        assert not (tmp_path / "name").exists()
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_commit_failure_rolls_back_logical_state(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.write_text("old1\n")
        second.write_text("old2\n")
        original_replace = patch_module.os.replace

        def replace(source, target):
            if Path(target) == second:
                raise OSError("injected commit failure")
            original_replace(source, target)

        monkeypatch.setattr(patch_module.os, "replace", replace)
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Update File: first
@@
-old1
+new1
*** Update File: second
@@
-old2
+new2
*** End Patch"""
        result = await _apply(executor, context, patch)
        assert result.results[0].error.code == "patch_commit_failed"
        assert first.read_text() == "old1\n"
        assert second.read_text() == "old2\n"
        assert tree.effects == ()
        assert not tuple(tmp_path.glob("*.patch"))

    asyncio.run(check())


def test_apply_patch_delete_rollback_restores_mode(tmp_path, monkeypatch) -> None:
    async def check() -> None:
        deleted = tmp_path / "deleted"
        failing = tmp_path / "failing"
        deleted.write_text("delete me\n")
        failing.write_text("old\n")
        os.chmod(deleted, 0o741)
        original_replace = patch_module.os.replace

        def replace(source, target):
            if Path(target) == failing:
                raise OSError("injected failure")
            original_replace(source, target)

        monkeypatch.setattr(patch_module.os, "replace", replace)
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Delete File: deleted
*** Update File: failing
@@
-old
+new
*** End Patch"""
        result = await _apply(executor, context, patch)
        assert result.results[0].error.code == "patch_commit_failed"
        assert deleted.read_text() == "delete me\n"
        assert deleted.stat().st_mode & 0o777 == 0o741
        assert failing.read_text() == "old\n"
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_incomplete_rollback_publishes_summary_and_effect(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.write_text("old1\n")
        second.write_text("old2\n")
        original_replace = patch_module.os.replace

        def replace(source, target):
            if Path(target) == second:
                raise OSError("injected commit failure")
            original_replace(source, target)

        def fail_restore(target, data, mode):
            raise OSError("injected rollback failure")

        monkeypatch.setattr(patch_module.os, "replace", replace)
        monkeypatch.setattr(patch_module, "_restore", fail_restore)
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Update File: first
@@
-old1
+new1
*** Update File: second
@@
-old2
+new2
*** End Patch"""
        result = await _apply(executor, context, patch)
        assert result.results[0].error.code == "patch_rollback_failed"
        assert first.read_text() == "new1\n"
        assert second.read_text() == "old2\n"
        assert len(tree.effects) == 1
        assert tree.effects[0].status is ToolEffectStatus.SUCCEEDED
        assert tree.retry_blockers[0].code.value == "settlement_uncertain"
        tree.mark_effects_committed((tree.effects[0].effect_id,))
        assert not retry_safe(tree.effects, tree.retry_blockers)
        summaries = [
            record
            for record in tree.execution_records
            if record.record_type is ExecutionRecordType.SUMMARY
        ]
        assert summaries[0].evidence["patch_target_states"] == (
            {"path": "first", "state": "committed"},
            {"path": "second", "state": "unchanged"},
        )

    asyncio.run(check())


def test_apply_patch_cancellation_during_settlement_retains_committed_effect(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        tree, executor, context = _runtime(tmp_path)
        original_commit = patch_module._PatchSettlement.commit

        def commit(handler):
            original_commit(handler)
            context.cancellation.cancel()

        monkeypatch.setattr(patch_module._PatchSettlement, "commit", commit)
        with pytest.raises(ToolBatchCancelled) as cancelled:
            await _apply(executor, context, _one("Add", "file", "+value\n"))
        assert len(cancelled.value.effects) == 1
        assert (tmp_path / "file").read_text() == "value\n"
        assert len(tree.effects) == 1
        assert tree.effects[0].status is ToolEffectStatus.SUCCEEDED

    asyncio.run(check())


def test_apply_patch_cancellation_before_barrier_removes_staged_files(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        tree, executor, context = _runtime(tmp_path)
        original_stage = patch_module._stage

        def stage(plans):
            original_stage(plans)
            context.cancellation.cancel()

        monkeypatch.setattr(patch_module, "_stage", stage)
        with pytest.raises(ToolBatchCancelled) as cancelled:
            await _apply(executor, context, _one("Add", "file", "+value\n"))
        assert cancelled.value.effects == ()
        assert not (tmp_path / "file").exists()
        assert not tuple(tmp_path.glob("*.patch"))
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_settlement_timeout_forces_full_rollback(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        target = tmp_path / "file"
        target.write_text("old\n")

        async def stalled_settle(handler):
            await asyncio.Event().wait()

        monkeypatch.setattr(patch_module._PatchSettlement, "settle", stalled_settle)
        tree, executor, context = _runtime(tmp_path, settlement_timeout=0.001)
        result = await _apply(
            executor,
            context,
            _one("Update", "file", "@@\n-old\n+new\n"),
        )
        assert result.results[0].error.code == "patch_commit_failed"
        assert target.read_text() == "old\n"
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_result_bound_is_closed_before_commit(tmp_path) -> None:
    async def check() -> None:
        tree, executor, context = _runtime(tmp_path, max_result_bytes=1)
        result = await _apply(executor, context, _one("Add", "file", "+x\n"))
        assert result.results[0].error.code == "patch_result_too_large"
        assert not (tmp_path / "file").exists()
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_config_rejects_bool_zero_and_wrong_filesystem(tmp_path) -> None:
    filesystem = FilesystemConfig(FilesystemWorkspace(tmp_path))
    for field in ("max_patch_bytes", "max_files", "max_file_bytes", "max_result_bytes"):
        with pytest.raises(ValueError):
            ApplyPatchConfig(filesystem, **{field: False})
        with pytest.raises(ValueError):
            ApplyPatchConfig(filesystem, **{field: 0})
    with pytest.raises(TypeError):
        ApplyPatchConfig(object())  # type: ignore[arg-type]
