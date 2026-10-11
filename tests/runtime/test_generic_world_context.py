"""World causal context does not impose application action or wrapper policies."""

import asyncio

from roboagent.message import ToolCall
from roboagent.runtime import ExecutionScopeState, RuntimeRunExecutionContext
from roboagent.tool import (
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutor,
    ToolJsonContent,
    ToolRegistry,
)
from tests.runtime.test_nested_execution import _tree


def tool(name, handler):
    return Tool(
        ToolDefinition(name, name, {"type": "object", "properties": {}}),
        handler,
        effect_kind=ToolEffectKind.SIDE_EFFECTING,
    )


def test_generic_turn_allows_multiple_side_effecting_tools():
    async def check():
        calls = []
        lifecycle = []

        class Resource:
            async def close(self):
                lifecycle.append("closed")

            async def force_close(self):
                raise AssertionError("Normal resource close must succeed.")

        async def mutate(args, context):
            calls.append(context.message_id)
            lifecycle.append("started")
            context.execution.register_resource(Resource())
            return ToolJsonContent("done")

        executor = ToolExecutor(registry=ToolRegistry((tool("write", mutate),)))
        runtime = RuntimeRunExecutionContext(_tree().root_scope)
        context = ToolContext(
            "root",
            "session",
            runtime.cancellation,
            runtime.tool_context(executor, "session"),
            "turn",
        )
        batch = await executor.execute(
            (ToolCall("one", "write"), ToolCall("two", "write")), context
        )
        assert all(result.error is None for result in batch.results)
        assert calls == ["turn", "turn"] and len(batch.effects) == 2
        assert lifecycle == ["started", "closed", "started", "closed"]
        assert runtime.scope_state is ExecutionScopeState.OPEN
        await runtime.finalize()
        assert runtime.scope_state is ExecutionScopeState.FROZEN

    asyncio.run(check())


def test_generic_wrapper_can_overlap_independent_nested_tools():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        started = []

        async def leaf(args, context):
            started.append(context.message_id)
            if len(started) == 2:
                entered.set()
            await release.wait()
            return ToolJsonContent("done")

        async def wrapper(args, context):
            first = asyncio.create_task(
                context.execution.execute_nested_tool("leaf", {})
            )
            second = asyncio.create_task(
                context.execution.execute_nested_tool("leaf", {})
            )
            await asyncio.wait_for(entered.wait(), 1)
            release.set()
            assert all(
                result.error is None for result in await asyncio.gather(first, second)
            )
            return ToolJsonContent("done")

        executor = ToolExecutor(
            registry=ToolRegistry((tool("wrapper", wrapper), tool("leaf", leaf)))
        )
        runtime = RuntimeRunExecutionContext(_tree().root_scope)
        context = ToolContext(
            "root",
            "session",
            runtime.cancellation,
            runtime.tool_context(executor, "session"),
            "turn",
        )
        batch = await executor.execute((ToolCall("wrapper", "wrapper"),), context)
        assert batch.results[0].error is None and started == ["turn", "turn"]
        await runtime.finalize()

    asyncio.run(check())
