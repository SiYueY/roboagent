from __future__ import annotations

import asyncio
import os
import stat

import pytest

from roboagent.message import ToolCall
from roboagent.runtime import (
    ExecutionBudgetConfig,
    ExecutionTree,
    RuntimeCancellation,
    RuntimeToolExecutionContext,
)
from roboagent.tool import (
    ApplyPatchConfig,
    FilesystemConfig,
    FilesystemWorkspace,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    create_apply_patch_tool,
)


def _runtime(tmp_path, **limits):
    config = ApplyPatchConfig(FilesystemConfig(FilesystemWorkspace(tmp_path)), **limits)
    tool = create_apply_patch_tool(config)
    tree = ExecutionTree(
        root_run_id="run",
        cancellation=RuntimeCancellation(),
        deadline=None,
        budget=ExecutionBudgetConfig(),
        settlement_timeout=1,
        cleanup_timeout=1,
        max_execution_records=64,
        max_record_evidence_bytes=512,
    )
    executor = ToolExecutor(registry=ToolRegistry((tool,)))
    context = ToolContext(
        "run",
        "session",
        tree.cancellation,
        RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
    )
    return tree, executor, context


async def _apply(executor, context, patch):
    return await executor.execute(
        (ToolCall("patch", "apply_patch", {"patch": patch}),), context
    )


def test_apply_patch_add_update_delete_transaction_and_effects(tmp_path) -> None:
    async def check() -> None:
        (tmp_path / "old.txt").write_text("one\ntwo\n", newline="")
        (tmp_path / "delete.txt").write_text("gone\n")
        os.chmod(tmp_path / "old.txt", 0o640)
        tree, executor, context = _runtime(tmp_path)
        patch = """*** Begin Patch
*** Add File: added.txt
+hello
*** Update File: old.txt
@@
 one
-two
+changed
*** Delete File: delete.txt
*** End Patch"""
        batch = await _apply(executor, context, patch)
        assert batch.results[0].error is None
        assert (tmp_path / "added.txt").read_bytes() == b"hello\n"
        assert (tmp_path / "old.txt").read_bytes() == b"one\nchanged\n"
        assert stat.S_IMODE((tmp_path / "old.txt").stat().st_mode) == 0o640
        assert not (tmp_path / "delete.txt").exists()
        assert [effect.content.value["operation"] for effect in tree.effects] == [
            "added",
            "modified",
            "deleted",
        ]
        assert all(not effect.transcript_committed for effect in tree.effects)

    asyncio.run(check())


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ("", "invalid_arguments"),
        ("*** Begin Patch\n*** End Patch", "invalid_patch"),
        ("*** Begin Patch\n*** Add File: ../x\n+x\n*** End Patch", "invalid_path"),
        (
            "*** Begin Patch\n*** Add File: a\n+x\n*** Add File: a\n+y\n*** End Patch",
            "duplicate_patch_target",
        ),
        ("*** Begin Patch\n*** Add File:a\n+x\n*** End Patch", "invalid_patch"),
        ("*** Begin Patch\n*** Add File: a\n+x\n*** End Patch\n\n", "invalid_patch"),
        (
            "*** Begin Patch\n*** Add File: a\n+\x00\n*** End Patch",
            "unsupported_binary_content",
        ),
    ],
)
def test_apply_patch_strict_grammar_is_side_effect_free(tmp_path, patch, code) -> None:
    async def check() -> None:
        tree, executor, context = _runtime(tmp_path)
        batch = await _apply(executor, context, patch)
        assert batch.results[0].error.code == code
        assert not tuple(tmp_path.iterdir())
        assert tree.effects == ()

    asyncio.run(check())


def test_apply_patch_unique_hunks_crlf_no_final_newline_and_result_bound(
    tmp_path,
) -> None:
    async def check() -> None:
        (tmp_path / "crlf.txt").write_bytes(b"a\r\nb\r\n")
        tree, executor, context = _runtime(tmp_path, max_result_bytes=96)
        patch = """*** Begin Patch
*** Update File: crlf.txt
@@
 a
-b
+last
\\ No newline at end of file
*** End Patch
"""
        batch = await _apply(executor, context, patch)
        assert batch.results[0].error is None
        assert (tmp_path / "crlf.txt").read_bytes() == b"a\r\nlast"
        assert batch.results[0].content[0].value["truncated"] is True

        (tmp_path / "ambiguous.txt").write_text("x\nx\n")
        next_tree, next_executor, next_context = _runtime(tmp_path)
        conflict = """*** Begin Patch
*** Update File: ambiguous.txt
@@
-x
+y
*** End Patch"""
        failed = await _apply(next_executor, next_context, conflict)
        assert failed.results[0].error.code == "patch_conflict"
        assert (tmp_path / "ambiguous.txt").read_text() == "x\nx\n"
        assert next_tree.effects == ()

    asyncio.run(check())


def test_apply_patch_rejects_symlink_and_changed_preimage(
    tmp_path, monkeypatch
) -> None:
    async def check() -> None:
        outside = tmp_path.parent / "outside-apply-patch.txt"
        outside.write_text("safe")
        (tmp_path / "link").symlink_to(outside)
        tree, executor, context = _runtime(tmp_path)
        patch = "*** Begin Patch\n*** Update File: link\n@@\n-safe\n+bad\n*** End Patch"
        batch = await _apply(executor, context, patch)
        assert batch.results[0].error.code == "symlink_not_allowed"
        assert outside.read_text() == "safe"
        assert tree.effects == ()

    asyncio.run(check())
