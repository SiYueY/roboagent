from __future__ import annotations

import asyncio

from roboagent.message import FrozenJsonObject, ToolCall
from roboagent.runtime import (
    ExecutionBudgetConfig,
    ExecutionRecordStatus,
    ExecutionRecordType,
    ExecutionTree,
    RuntimeCancellation,
    RuntimeToolExecutionContext,
    SupplementalExecutionRecord,
)
from roboagent.tool import (
    CompositeToolOutcome,
    ToolErrorInfo,
    ToolExecutionFailure,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectRecord,
    ToolEffectReporting,
    ToolEffectStatus,
    ToolExecutor,
    ToolExecutorConfig,
    ToolBatchAborted,
    ToolBatchCancelled,
    ToolJsonContent,
    ToolRegistry,
    ToolTextContent,
)


def _tree() -> ExecutionTree:
    cancellation = RuntimeCancellation()
    return ExecutionTree(
        root_run_id="run",
        cancellation=cancellation,
        deadline=None,
        budget=ExecutionBudgetConfig(),
        settlement_timeout=1,
        cleanup_timeout=1,
        max_execution_records=16,
        max_record_evidence_bytes=256,
    )


def test_composite_content_is_not_materialized_effects_register_and_outer_record_is_automatic() -> (
    None
):
    async def check() -> None:
        tree = _tree()

        async def handler(arguments, context):
            effect = ToolEffectRecord(
                "call",
                "composite",
                ToolEffectKind.SIDE_EFFECTING,
                ToolEffectStatus.SUCCEEDED,
                content=ToolTextContent("evidence"),
            )
            return CompositeToolOutcome(
                (ToolJsonContent({"ok": True}),), effects=(effect,)
            )

        class MustNotMaterialize:
            async def materialize(self, *args, **kwargs):
                raise AssertionError("composite content was materialized")

        tool = Tool(
            ToolDefinition(
                "composite", "Composite test.", FrozenJsonObject({"type": "object"})
            ),
            handler,
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
            effect_reporting=ToolEffectReporting.COMPOSITE,
        )
        executor = ToolExecutor(
            registry=ToolRegistry((tool,)), result_materializer=MustNotMaterialize()
        )
        cancellation = tree.cancellation
        context = ToolContext(
            "run",
            "session",
            cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        batch = await executor.execute((ToolCall("call", "composite"),), context)
        assert batch.results[0].content == (ToolJsonContent({"ok": True}),)
        assert len(tree.effects) == 1 and tree.effects[0].effect_id is not None
        assert len(tree.execution_records) == 1
        assert tree.execution_records[0].record_type is ExecutionRecordType.TOOL

    asyncio.run(check())


def test_nested_tool_uses_same_executor_and_consumes_only_nested_budget() -> None:
    async def check() -> None:
        tree = _tree()
        calls: list[str] = []

        async def leaf(arguments, context):
            calls.append("leaf")
            return ToolTextContent("nested")

        async def composite(arguments, context):
            first = await context.execution.execute_nested_tool("leaf", {})
            second = await context.execution.execute_nested_tool("leaf", {})
            return CompositeToolOutcome(
                (ToolTextContent(first.content[0].text + second.content[0].text),)
            )

        tools = (
            Tool(
                ToolDefinition("leaf", "Leaf.", FrozenJsonObject({"type": "object"})),
                leaf,
            ),
            Tool(
                ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
                composite,
                effect_reporting=ToolEffectReporting.COMPOSITE,
            ),
        )
        executor = ToolExecutor(registry=ToolRegistry(tools))
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        batch = await executor.execute((ToolCall("outer", "outer"),), context)
        assert batch.results[0].content == (ToolTextContent("nestednested"),)
        assert calls == ["leaf", "leaf"]
        assert tree.budget_view.remaining_nested_tool_calls == 254
        assert len(tree.execution_records) == 3

    asyncio.run(check())


def test_composite_after_hook_failure_keeps_effect_and_redactor_fails_closed() -> None:
    async def check() -> None:
        tree = _tree()

        async def handler(arguments, context):
            return CompositeToolOutcome(
                (ToolTextContent("done"),),
                effects=(
                    ToolEffectRecord(
                        "call",
                        "outer",
                        ToolEffectKind.SIDE_EFFECTING,
                        ToolEffectStatus.SUCCEEDED,
                        content=ToolTextContent("changed"),
                    ),
                ),
            )

        class Hook:
            async def after_tool(self, context, result):
                raise RuntimeError("hook")

        def broken_redactor(arguments):
            raise RuntimeError("redaction")

        tool = Tool(
            ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
            handler,
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
            effect_reporting=ToolEffectReporting.COMPOSITE,
            record_redactor=broken_redactor,
        )
        executor = ToolExecutor(registry=ToolRegistry((tool,)), hooks=(Hook(),))
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        try:
            await executor.execute(
                (ToolCall("call", "outer", {"secret": "value"}),), context
            )
        except ToolBatchAborted:
            pass
        else:
            raise AssertionError("hook failure must abort the batch")
        assert len(tree.effects) == 1
        assert tree.execution_records[0].arguments_preview is None
        assert tree.execution_records[0].status.value == "failed"

    asyncio.run(check())


def test_invalid_nested_request_does_not_consume_budget() -> None:
    async def check() -> None:
        tree = ExecutionTree(
            root_run_id="run",
            cancellation=RuntimeCancellation(),
            deadline=None,
            budget=ExecutionBudgetConfig(max_nested_tool_calls=1),
            settlement_timeout=1,
            cleanup_timeout=1,
            max_execution_records=16,
            max_record_evidence_bytes=256,
        )

        async def outer(arguments, context):
            invalid = await context.execution.execute_nested_tool("leaf", {})
            valid = await context.execution.execute_nested_tool("leaf", {"value": 1})
            exhausted = await context.execution.execute_nested_tool(
                "leaf", {"value": 2}
            )
            return CompositeToolOutcome(
                (
                    ToolTextContent("done"),
                    ToolJsonContent(
                        {
                            "invalid": invalid.error.code,
                            "valid": valid.error is None,
                            "exhausted": exhausted.error.code,
                        }
                    ),
                )
            )

        leaf_schema = FrozenJsonObject(
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            }
        )
        tools = (
            Tool(
                ToolDefinition("leaf", "Leaf.", leaf_schema),
                lambda arguments, context: ToolTextContent("ok"),
            ),
            Tool(
                ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
                outer,
                effect_reporting=ToolEffectReporting.COMPOSITE,
            ),
        )
        executor = ToolExecutor(registry=ToolRegistry(tools))
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        result = await executor.execute((ToolCall("outer", "outer"),), context)
        values = result.results[0].content
        assert values[1].value == {
            "invalid": "invalid_arguments",
            "valid": True,
            "exhausted": "nested_tool_budget_exceeded",
        }

    asyncio.run(check())


def test_composite_declared_failure_does_not_synthesize_an_outer_effect() -> None:
    async def check() -> None:
        tree = _tree()

        async def handler(arguments, context):
            raise ToolExecutionFailure(ToolErrorInfo("not_started", "Not started."))

        tool = Tool(
            ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
            handler,
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
            effect_reporting=ToolEffectReporting.COMPOSITE,
        )
        executor = ToolExecutor(registry=ToolRegistry((tool,)))
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        result = await executor.execute((ToolCall("call", "outer"),), context)
        assert result.results[0].error.code == "not_started"
        assert result.effects == ()
        assert tree.effects == ()

    asyncio.run(check())


def test_composite_cancellation_settlement_keeps_all_effects_and_summary() -> None:
    async def check() -> None:
        tree = _tree()

        async def handler(arguments, context):
            effects = tuple(
                ToolEffectRecord(
                    f"leaf-{index}",
                    "write",
                    ToolEffectKind.SIDE_EFFECTING,
                    ToolEffectStatus.SUCCEEDED,
                    content=ToolTextContent(f"effect-{index}"),
                )
                for index in range(2)
            )
            context.cancellation.cancel()
            return CompositeToolOutcome(
                (ToolTextContent("settled"),),
                effects=effects,
                records=(
                    SupplementalExecutionRecord(
                        ExecutionRecordStatus.SUCCEEDED,
                        evidence=FrozenJsonObject({"settled": True}),
                    ),
                ),
            )

        tool = Tool(
            ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
            handler,
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
            effect_reporting=ToolEffectReporting.COMPOSITE,
        )
        executor = ToolExecutor(registry=ToolRegistry((tool,)))
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        try:
            await executor.execute((ToolCall("call", "outer"),), context)
        except ToolBatchCancelled as cancelled:
            assert len(cancelled.effects) == 2
        else:
            raise AssertionError("tool-scope cancellation must cancel the batch")
        assert [effect.call_id for effect in tree.effects] == ["leaf-0", "leaf-1"]
        assert any(
            record.record_type is ExecutionRecordType.SUMMARY
            for record in tree.execution_records
        )

    asyncio.run(check())


def test_tool_cancellation_grace_cannot_cut_off_active_settlement() -> None:
    async def check() -> None:
        tree = _tree()
        settled = asyncio.Event()

        class Handler:
            async def settle(self):
                await asyncio.sleep(0.03)
                settled.set()

            async def force_settle(self):
                raise AssertionError("settlement should finish before timeout")

        async def handler(arguments, context):
            async with context.execution.settlement_barrier(
                handler=Handler(), timeout=0.5
            ):
                context.cancellation.cancel()
            return CompositeToolOutcome((ToolTextContent("unreachable"),))

        tool = Tool(
            ToolDefinition("outer", "Outer.", FrozenJsonObject({"type": "object"})),
            handler,
            effect_reporting=ToolEffectReporting.COMPOSITE,
        )
        executor = ToolExecutor(
            registry=ToolRegistry((tool,)),
            config=ToolExecutorConfig(cancellation_grace_period=0.001),
        )
        context = ToolContext(
            "run",
            "session",
            tree.cancellation,
            RuntimeToolExecutionContext(tree.root_scope, executor, "session"),
        )
        try:
            await executor.execute((ToolCall("call", "outer"),), context)
        except ToolBatchCancelled:
            pass
        else:
            raise AssertionError("cancelled settlement must cancel the Tool batch")
        assert settled.is_set()

    asyncio.run(check())
