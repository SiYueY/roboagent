import asyncio

from roboagent import Agent
from roboagent.message import AssistantMessage, ToolCall, UserMessage
from roboagent.runtime import RunStatus
from roboagent.tool import Tool, ToolDefinition, ToolJsonContent, ToolRegistry
from tests.integration.test_embodied_loop import ScriptedModel
from tests.world.test_world import claim, observation, world


def tool(name, handler):
    return Tool(
        ToolDefinition(
            name,
            name,
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        handler,
    )


def test_nested_calls_inherit_exact_source_and_message_identity():
    async def check():
        w = world()
        await w.observe(observation(), claims=[claim()])
        seen = []

        async def leaf(args, context):
            seen.append(context)
            return ToolJsonContent("ok")

        async def wrapper(args, context):
            seen.append(context)
            result = await context.execution.execute_nested_tool("leaf", {})
            assert result.error is None
            return ToolJsonContent("ok")

        model = ScriptedModel(
            AssistantMessage(
                tool_calls=(ToolCall("wrap", "wrapper"), ToolCall("direct", "leaf"))
            ),
            AssistantMessage("done"),
        )
        from dataclasses import replace

        model.capabilities = replace(model.capabilities, parallel_tool_calls=True)
        agent = Agent(
            model,
            world=w,
            tool_registry=ToolRegistry((tool("wrapper", wrapper), tool("leaf", leaf))),
        )
        session = agent.new_session()
        try:
            result = await session.run(UserMessage("go"))
            assert result.status is RunStatus.COMPLETED, result.error
        finally:
            await session.close()
        assert len(seen) == 3
        assert all(
            c.decision_world_snapshot is seen[0].decision_world_snapshot for c in seen
        )
        assert all(c.message_id == seen[0].message_id for c in seen)
        assert seen[0].message_id is not None

    asyncio.run(check())


def test_child_agent_captures_its_own_world():
    async def check():
        parent_world, child_world = world(), world()
        await parent_world.observe(observation(), claims=[claim(value="red")])
        await child_world.observe(observation(), claims=[claim(value="blue")])
        captured = []

        async def leaf(args, context):
            captured.append(context)
            return ToolJsonContent("ok")

        child_model = ScriptedModel(
            AssistantMessage(tool_calls=(ToolCall("child", "leaf"),)),
            AssistantMessage("done"),
        )
        child = Agent(
            child_model,
            world=child_world,
            tool_registry=ToolRegistry((tool("leaf", leaf),)),
        )
        parent_model = ScriptedModel(
            AssistantMessage(
                tool_calls=(ToolCall("delegate", "child_agent", {"task": "go"}),)
            ),
            AssistantMessage("done"),
        )
        parent = Agent(
            parent_model,
            world=parent_world,
            tool_registry=ToolRegistry(
                (child.as_tool(name="child_agent", description="child"),)
            ),
        )
        session = parent.new_session()
        try:
            result = await session.run(UserMessage("go"))
            assert result.status is RunStatus.COMPLETED, result.error
        finally:
            await session.close()
        assert captured[0].decision_world_snapshot.entries[0].value == "blue"
        assert captured[0].run_id != captured[0].execution.lineage.root_run_id

    asyncio.run(check())


def test_physical_capable_wrapper_rejects_overlapping_nested_execution():
    async def check():
        from examples.embodied.contracts import PhysicalPolicy
        from roboagent.runtime.execution import RuntimeRunExecutionContext
        from tests.runtime.test_nested_execution import _tree
        from roboagent.tool import ToolContext, ToolExecutor

        started, release = asyncio.Event(), asyncio.Event()
        results = []

        async def leaf(args, context):
            started.set()
            await release.wait()
            return ToolJsonContent("ok")

        async def wrapper(args, context):
            first = asyncio.create_task(
                context.execution.execute_nested_tool("leaf", {})
            )
            await started.wait()
            results.append(await context.execution.execute_nested_tool("leaf", {}))
            release.set()
            results.append(await first)
            return ToolJsonContent("ok")

        executor = ToolExecutor(
            registry=ToolRegistry((tool("wrapper", wrapper), tool("leaf", leaf))),
            policy=PhysicalPolicy(frozenset(), frozenset(("wrapper",))),
        )
        runtime = RuntimeRunExecutionContext(_tree().root_scope)
        execution = runtime.tool_context(executor, "session")
        context = ToolContext(
            "root", "session", execution.cancellation, execution, "model"
        )
        await executor.execute((ToolCall("wrapper", "wrapper"),), context)
        assert results[0].error.code == "concurrent_nested_execution"
        assert results[1].error is None

    asyncio.run(check())
