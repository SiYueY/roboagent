from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from roboagent.message import FrozenJsonObject, ToolCall
from roboagent.runtime import RuntimeCancellation
from roboagent.tool import (
    FilesystemConfig,
    ShellConfig,
    Tool,
    ToolBatchAborted,
    ToolBatchCancelled,
    ToolContext,
    ToolDecision,
    ToolDefinition,
    ToolExecutionMode,
    ToolEffectKind,
    ToolEffectUnknown,
    ToolEffectStatus,
    ToolErrorInfo,
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutionFailure,
    ToolJsonContent,
    ToolRegistrationError,
    ToolRegistry,
    ToolTextContent,
    Workspace,
    create_filesystem_tools,
    create_shell_tool,
    retry_safe,
)


def _definition(name: str = "work") -> ToolDefinition:
    return ToolDefinition(name, "Do work.", FrozenJsonObject({"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}))


def _context() -> ToolContext:
    return ToolContext("run", "session", RuntimeCancellation())


def test_registry_validation_order_and_explicit_replace() -> None:
    async def handler(arguments, context):
        return ToolTextContent("ok")

    first = Tool(_definition(), handler)
    second = Tool(_definition(), handler, ToolExecutionMode.CONCURRENT)
    registry = ToolRegistry((first,))
    with pytest.raises(ToolRegistrationError):
        registry.register(second)
    registry.register(second, replace=True)
    assert registry.get("work") is second
    assert [item.name for item in registry.definitions()] == ["work"]
    with pytest.raises(ToolRegistrationError):
        ToolDefinition("bad name", "x", FrozenJsonObject({"type": "object"}))
    with pytest.raises(ToolRegistrationError):
        ToolDefinition("valid", "x", FrozenJsonObject({"type": "array"}))


def test_policy_runs_before_lookup_and_validation() -> None:
    async def check() -> None:
        seen: list[tuple[str, bool]] = []

        class Policy:
            async def evaluate(self, call, tool, context):
                seen.append((call.name, tool is None))
                return ToolDecision.REJECT

        tool = Tool(_definition(), lambda arguments, context: ToolTextContent("never"))
        batch = await ToolExecutor(registry=ToolRegistry((tool,)), policy=Policy()).execute(
            (ToolCall("a", "missing", FrozenJsonObject()), ToolCall("b", "work", FrozenJsonObject())), _context()
        )
        assert seen == [("missing", True), ("work", False)]
        assert [result.error.code for result in batch.results if result.error] == ["rejected", "rejected"]
        assert batch.effects == ()

    asyncio.run(check())


def test_concurrent_batch_is_bounded_and_results_keep_call_order() -> None:
    async def check() -> None:
        active = 0
        peak = 0

        async def handler(arguments, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep((4 - arguments["value"]) / 100)
            active -= 1
            return ToolJsonContent(arguments)

        tool = Tool(_definition(), handler, ToolExecutionMode.CONCURRENT)
        calls = tuple(ToolCall(str(value), "work", FrozenJsonObject({"value": value})) for value in range(4))
        batch = await ToolExecutor(registry=ToolRegistry((tool,)), config=ToolExecutorConfig(max_concurrency=2)).execute(calls, _context())
        assert peak == 2
        assert [result.call_id for result in batch.results] == ["0", "1", "2", "3"]
        assert all(not effect.transcript_committed for effect in batch.effects)

    asyncio.run(check())


def test_fail_run_aborts_and_retry_safe_formula() -> None:
    async def check() -> None:
        class Policy:
            async def evaluate(self, call, tool, context):
                return ToolDecision.FAIL_RUN

        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry(), policy=Policy()).execute((ToolCall("x", "missing"),), _context())
        assert caught.value.reason.code == "policy_fail_run"

    asyncio.run(check())
    assert retry_safe(())


def test_timeout_is_a_terminal_result_and_invalid_output_aborts() -> None:
    async def check() -> None:
        async def slow(arguments, context):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return ToolTextContent("late")

        timed = Tool(_definition(), slow, timeout=0.001)
        batch = await ToolExecutor(
            registry=ToolRegistry((timed,)),
            config=ToolExecutorConfig(cancellation_grace_period=0.01),
        ).execute((ToolCall("t", "work", FrozenJsonObject({"value": 1})),), _context())
        assert batch.results[0].error is not None and batch.results[0].error.code == "timeout"
        assert batch.effects[0].status is ToolEffectStatus.SUCCEEDED

        invalid = Tool(_definition("invalid"), lambda arguments, context: "legacy output")
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry((invalid,))).execute(
                (ToolCall("i", "invalid", FrozenJsonObject({"value": 1})),), _context()
            )
        assert caught.value.reason.code == "tool_contract_error"
        assert caught.value.effects[0].error is not None
        assert caught.value.effects[0].error.code == "invalid_tool_output"
        assert caught.value.effects[0].status is ToolEffectStatus.FAILED

    asyncio.run(check())


def test_side_effecting_generic_exception_is_unknown() -> None:
    async def check() -> None:
        async def raises(arguments, context):
            raise ConnectionError("acknowledgement lost")

        call = ToolCall("x", "work", FrozenJsonObject({"value": 1}))
        read_only = Tool(_definition(), raises, effect_kind=ToolEffectKind.READ_ONLY)
        read_batch = await ToolExecutor(registry=ToolRegistry((read_only,))).execute((call,), _context())
        assert read_batch.results[0].error is not None
        assert read_batch.results[0].error.code == "execution_error"
        assert read_batch.effects[0].status is ToolEffectStatus.FAILED
        assert retry_safe(read_batch.effects)

        side_effecting = Tool(_definition(), raises, effect_kind=ToolEffectKind.SIDE_EFFECTING)
        side_batch = await ToolExecutor(registry=ToolRegistry((side_effecting,))).execute((call,), _context())
        assert side_batch.results[0].error is not None
        assert side_batch.results[0].error.code == "execution_error"
        effect = side_batch.effects[0]
        assert effect.status is ToolEffectStatus.UNKNOWN
        assert effect.error is not None and effect.error.code == "effect_unknown"
        assert not retry_safe(side_batch.effects)

    asyncio.run(check())


def test_side_effecting_invalid_output_is_unknown() -> None:
    async def check() -> None:
        invalid = Tool(
            _definition(),
            lambda arguments, context: {"legacy": True},
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
        )
        with pytest.raises(ToolBatchAborted) as caught:
            await ToolExecutor(registry=ToolRegistry((invalid,))).execute(
                (ToolCall("x", "work", FrozenJsonObject({"value": 1})),), _context()
            )
        assert caught.value.reason.code == "tool_contract_error"
        effect = caught.value.effects[0]
        assert effect.status is ToolEffectStatus.UNKNOWN
        assert effect.error is not None and effect.error.code == "invalid_tool_output"
        assert not retry_safe(caught.value.effects)

    asyncio.run(check())


def test_timeout_effect_status_uses_cleanup_evidence() -> None:
    async def check() -> None:
        async def interrupted(arguments, context):
            await asyncio.Event().wait()

        async def completed(arguments, context):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ToolTextContent("applied")

        async def stubborn(arguments, context):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.Event().wait()

        async def failed(arguments, context):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise ToolExecutionFailure(ToolErrorInfo("not_applied", "Action was not applied.")) from exc

        async def uncertain(arguments, context):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise ToolEffectUnknown(ToolErrorInfo("ack_lost", "Action acknowledgement was lost.")) from exc

        cases = (
            (ToolEffectKind.READ_ONLY, interrupted, ToolEffectStatus.TIMED_OUT, "timeout"),
            (ToolEffectKind.SIDE_EFFECTING, interrupted, ToolEffectStatus.UNKNOWN, "timeout"),
            (ToolEffectKind.READ_ONLY, stubborn, ToolEffectStatus.TIMED_OUT, "timeout"),
            (ToolEffectKind.SIDE_EFFECTING, stubborn, ToolEffectStatus.UNKNOWN, "timeout"),
            (ToolEffectKind.SIDE_EFFECTING, completed, ToolEffectStatus.SUCCEEDED, None),
            (ToolEffectKind.SIDE_EFFECTING, failed, ToolEffectStatus.FAILED, "not_applied"),
            (ToolEffectKind.SIDE_EFFECTING, uncertain, ToolEffectStatus.UNKNOWN, "ack_lost"),
        )
        for effect_kind, handler, expected_status, expected_error in cases:
            tool = Tool(_definition(), handler, effect_kind=effect_kind, timeout=0.001)
            batch = await ToolExecutor(
                registry=ToolRegistry((tool,)),
                config=ToolExecutorConfig(cancellation_grace_period=0.01),
            ).execute((ToolCall("t", "work", FrozenJsonObject({"value": 1})),), _context())
            assert batch.results[0].error is not None and batch.results[0].error.code == "timeout"
            effect = batch.effects[0]
            assert effect.status is expected_status
            assert (effect.error.code if effect.error else None) == expected_error
            expected_retry_safe = not (
                effect_kind is ToolEffectKind.SIDE_EFFECTING
                and expected_status in {ToolEffectStatus.SUCCEEDED, ToolEffectStatus.UNKNOWN}
            )
            assert retry_safe(batch.effects) is expected_retry_safe

    asyncio.run(check())


def test_cancel_effect_status_is_conservative_for_side_effects() -> None:
    async def check(effect_kind: ToolEffectKind) -> tuple[ToolEffectStatus, str]:
        started = asyncio.Event()

        async def handler(arguments, context):
            started.set()
            await asyncio.Event().wait()

        cancellation = RuntimeCancellation()
        context = ToolContext("run", "session", cancellation)
        tool = Tool(_definition(), handler, effect_kind=effect_kind)
        execution = asyncio.create_task(
            ToolExecutor(registry=ToolRegistry((tool,))).execute(
                (ToolCall("c", "work", FrozenJsonObject({"value": 1})),), context
            )
        )
        await started.wait()
        cancellation.cancel()
        with pytest.raises(ToolBatchCancelled) as caught:
            await execution
        effect = caught.value.effects[0]
        assert not effect.transcript_committed
        assert effect.error is not None
        assert retry_safe(caught.value.effects) is (effect_kind is ToolEffectKind.READ_ONLY)
        return effect.status, effect.error.code

    assert asyncio.run(check(ToolEffectKind.READ_ONLY)) == (ToolEffectStatus.CANCELLED, "cancelled")
    assert asyncio.run(check(ToolEffectKind.SIDE_EFFECTING)) == (ToolEffectStatus.UNKNOWN, "effect_unknown")


def test_filesystem_tools_are_scoped_atomic_and_deterministic(tmp_path: Path) -> None:
    async def check() -> None:
        workspace = Workspace(tmp_path)
        tools = {tool.definition.name: tool for tool in create_filesystem_tools(FilesystemConfig(workspace))}
        context = _context()
        written = await tools["write_file"].execute(FrozenJsonObject({"path": "a/file.txt", "content": "héllo", "create_parents": True}), context)
        assert isinstance(written, ToolJsonContent) and written.value["bytes_written"] == 6
        read = await tools["read_file"].execute(FrozenJsonObject({"path": "a/file.txt", "offset": 1, "limit": 3}), context)
        assert isinstance(read, ToolTextContent) and read.text == "éll"
        await tools["write_file"].execute(FrozenJsonObject({"path": "b.txt", "content": "x"}), context)
        found = await tools["find_files"].execute(FrozenJsonObject({"pattern": "**/*.txt"}), context)
        assert isinstance(found, ToolJsonContent)
        assert [item["path"] for item in found.value["items"]] == ["a/file.txt", "b.txt"]
        with pytest.raises(Exception):
            await tools["read_file"].execute(FrozenJsonObject({"path": "../outside"}), context)

    asyncio.run(check())


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell only")
def test_shell_is_noninteractive_scoped_and_bounded(tmp_path: Path) -> None:
    async def check() -> None:
        (tmp_path / "sub").mkdir()
        before = dict(os.environ)
        shell = create_shell_tool(ShellConfig(Workspace(tmp_path), max_stdout_bytes=3, env=FrozenJsonObject({"ROBOAGENT_TEST": "yes"})))
        result = await shell.execute(FrozenJsonObject({"command": "printf 12345; printf err >&2; printf $ROBOAGENT_TEST > marker", "cwd": "sub"}), _context())
        assert isinstance(result, ToolJsonContent)
        assert result.value["stdout"] == "123" and result.value["stdout_truncated"] is True
        assert result.value["stderr"] == "err"
        assert (tmp_path / "sub" / "marker").read_text() == "yes"
        assert dict(os.environ) == before

        background = create_shell_tool(ShellConfig(Workspace(tmp_path), cancellation_grace_period=0.02))
        completed = await asyncio.wait_for(
            background.execute(FrozenJsonObject({"command": "sleep 10 &"}), _context()),
            timeout=1,
        )
        assert isinstance(completed, ToolJsonContent) and completed.value["exit_code"] == 0

    asyncio.run(check())
