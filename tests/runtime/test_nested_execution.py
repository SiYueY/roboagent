from __future__ import annotations

import asyncio

import pytest

from roboagent.agent import RunConfig
from roboagent.agent import Agent
from roboagent.message import AssistantMessage, FrozenJsonObject, ToolCall, UserMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageUpdated,
)
from roboagent.runtime import (
    ContributionId,
    ExecutionBudgetConfig,
    ExecutionContribution,
    ExecutionInvariantError,
    ExecutionRequestError,
    ExecutionRecordStatus,
    ExecutionRecordType,
    ExecutionScopeState,
    ExecutionTree,
    RetryBlocker,
    RetryBlockerCode,
    CancellationOrigin,
    CancellationReason,
    RuntimeCancellation,
    RuntimeRunExecutionContext,
    RunEventEmitter,
    SettlementError,
    SupplementalExecutionRecord,
    UsageContribution,
    UsageKnowledge,
)
from roboagent.runtime.types import RunContext
from roboagent.tool import (
    EffectCertainty,
    ToolContext,
    ToolEffectKind,
    ToolEffectRecord,
    ToolEffectReporting,
    ToolEffectStatus,
    ToolTextContent,
    retry_safe,
    Tool,
    ToolDefinition,
    ToolRegistry,
)


def _tree(**overrides) -> ExecutionTree:
    values = {
        "root_run_id": "root",
        "cancellation": RuntimeCancellation(),
        "deadline": None,
        "budget": ExecutionBudgetConfig(),
        "settlement_timeout": 0.02,
        "cleanup_timeout": 0.02,
        "max_execution_records": 4,
        "max_record_evidence_bytes": 128,
    }
    values.update(overrides)
    return ExecutionTree(**values)


def test_context_construction_is_backward_compatible_and_runtime_identity_is_strict() -> (
    None
):
    cancellation = RuntimeCancellation()
    assert RunContext("run", "session", cancellation).execution is None
    assert ToolContext("run", "session", cancellation).execution is None

    tree = _tree(root_run_id="run", cancellation=cancellation)
    execution = RuntimeRunExecutionContext(tree.root_scope)
    context = RunContext("run", "session", cancellation, execution)
    assert context.execution is execution
    assert execution.lineage.root_run_id == execution.lineage.execution_run_id == "run"
    assert execution.cancellation is cancellation
    with pytest.raises(ValueError):
        RunContext("wrong", "session", cancellation, execution)


def test_v13_protocol_enums_are_string_enums() -> None:
    values = (
        ToolEffectReporting.COMPOSITE,
        UsageKnowledge.UNKNOWN,
        ExecutionRecordStatus.CANCELLED,
        ExecutionRecordType.SUMMARY,
        EffectCertainty.CERTAIN_NO_EFFECT,
        RetryBlockerCode.SETTLEMENT_UNCERTAIN,
    )
    assert all(isinstance(value, str) and value == value.value for value in values)


def test_run_config_v13_defaults_and_validation() -> None:
    config = RunConfig()
    assert config.execution_budget == ExecutionBudgetConfig(4, 32, 256)
    assert (config.settlement_timeout, config.cleanup_timeout) == (10.0, 5.0)
    assert (config.max_execution_records, config.max_record_evidence_bytes) == (
        4096,
        4096,
    )
    assert config.max_child_artifact_bytes == 64 * 1024 * 1024
    for value in (True, -1):
        with pytest.raises(ValueError):
            ExecutionBudgetConfig(max_nested_tool_calls=value)  # type: ignore[arg-type]
    for field in (
        "max_execution_records",
        "max_record_evidence_bytes",
        "max_child_artifact_bytes",
    ):
        with pytest.raises(ValueError):
            RunConfig(**{field: 0})


def test_scope_lifecycle_sequences_and_contribution_exactly_once() -> None:
    tree = _tree()
    root = tree.root_scope
    first = root.child_tool(tool_call_id="a")
    second = root.child_tool(tool_call_id="b")
    assert [root.scope_sequence, first.scope_sequence, second.scope_sequence] == [
        0,
        1,
        2,
    ]
    assert tree.event_sequences.next() == 0
    assert tree.record_sequences.next() == 0

    contribution = ExecutionContribution(ContributionId(first.lineage.scope_id, 0))
    first.contribute(contribution)
    first.contribute(contribution)
    with pytest.raises(ExecutionInvariantError):
        first.contribute(
            ExecutionContribution(
                ContributionId(first.lineage.scope_id, 0),
                usage=UsageContribution(UsageKnowledge.KNOWN, Usage(1, 1, 2)),
            )
        )
    first.begin_closing()
    assert first.state is ExecutionScopeState.CLOSING
    with pytest.raises(ExecutionInvariantError):
        first.contribute(ExecutionContribution(first.next_contribution_id()))
    first.contribute(
        ExecutionContribution(first.next_contribution_id()), settlement=True
    )
    first.freeze()
    with pytest.raises(ExecutionInvariantError):
        first.contribute(
            ExecutionContribution(first.next_contribution_id()), settlement=True
        )


def test_child_agent_depth_and_breadth_budget_rejections_do_not_overconsume() -> None:
    depth = _tree(budget=ExecutionBudgetConfig(max_agent_depth=0))
    with pytest.raises(ExecutionRequestError) as exceeded:
        depth.new_child_run_scope(
            parent=depth.root_scope,
            execution_run_id="child",
            cancellation=RuntimeCancellation(depth.cancellation),
            deadline=None,
            agent_tool_name="delegate",
        )
    assert exceeded.value.code == "agent_depth_exceeded"
    assert depth.child_runs_used == 0

    breadth = _tree(budget=ExecutionBudgetConfig(max_child_runs=1))
    first = breadth.new_child_run_scope(
        parent=breadth.root_scope,
        execution_run_id="first",
        cancellation=RuntimeCancellation(breadth.cancellation),
        deadline=None,
        agent_tool_name="delegate",
    )
    assert first.lineage.agent_depth == 1
    with pytest.raises(ExecutionRequestError) as exhausted:
        breadth.new_child_run_scope(
            parent=breadth.root_scope,
            execution_run_id="second",
            cancellation=RuntimeCancellation(breadth.cancellation),
            deadline=None,
            agent_tool_name="delegate",
        )
    assert exhausted.value.code == "child_run_budget_exceeded"
    assert breadth.child_runs_used == 1


def test_child_deadline_is_bounded_and_linked_cancellation_is_one_way() -> None:
    parent_cancellation = RuntimeCancellation()
    tree = _tree(cancellation=parent_cancellation, deadline=100.0)
    child_cancellation = RuntimeCancellation(parent_cancellation)
    child = tree.new_child_run_scope(
        parent=tree.root_scope,
        execution_run_id="child",
        cancellation=child_cancellation,
        deadline=200.0,
        agent_tool_name="delegate",
    )
    assert child.deadline == 100.0
    child_cancellation.cancel(CancellationReason.USER, CancellationOrigin.EXTERNAL)
    assert child_cancellation.cancelled
    assert child_cancellation.origin is CancellationOrigin.EXTERNAL
    assert not parent_cancellation.cancelled

    parent = RuntimeCancellation()
    descendant = RuntimeCancellation(parent)
    parent.cancel(CancellationReason.USER, CancellationOrigin.EXTERNAL)
    assert descendant.cancelled
    assert descendant.origin is CancellationOrigin.PARENT


def test_usage_tristate_and_partial_unknown_fields() -> None:
    tree = _tree()
    scope = tree.root_scope
    assert tree.usage_result == (None, None)
    scope.contribute(
        ExecutionContribution(
            scope.next_contribution_id(),
            usage=UsageContribution(UsageKnowledge.KNOWN, Usage(None, 2, None)),
        )
    )
    scope.contribute(
        ExecutionContribution(
            scope.next_contribution_id(),
            usage=UsageContribution(UsageKnowledge.KNOWN, Usage(3, 4, 7)),
        )
    )
    assert tree.usage_result == (Usage(None, 6, None), True)
    scope.contribute(
        ExecutionContribution(
            scope.next_contribution_id(),
            usage=UsageContribution(UsageKnowledge.UNKNOWN, None),
        )
    )
    assert tree.usage_result == (None, False)


def test_effect_certainty_matrix_order_commit_and_retry_truth() -> None:
    tree = _tree()
    left = tree.root_scope.child_tool(tool_call_id="left")
    right = tree.root_scope.child_tool(tool_call_id="right")
    effect_right = ToolEffectRecord(
        "right",
        "write",
        ToolEffectKind.SIDE_EFFECTING,
        ToolEffectStatus.SUCCEEDED,
        content=ToolTextContent("right"),
        effect_id=right.next_effect_id(),
        certainty=EffectCertainty.CERTAIN,
    )
    effect_left = ToolEffectRecord(
        "left",
        "write",
        ToolEffectKind.SIDE_EFFECTING,
        ToolEffectStatus.SUCCEEDED,
        content=ToolTextContent("left"),
        effect_id=left.next_effect_id(),
        certainty=EffectCertainty.CERTAIN,
    )
    right.contribute(
        ExecutionContribution(right.next_contribution_id(), effects=(effect_right,))
    )
    left.contribute(
        ExecutionContribution(left.next_contribution_id(), effects=(effect_left,))
    )
    assert [effect.call_id for effect in tree.effects] == ["left", "right"]
    assert not retry_safe(tree.effects)
    tree.mark_effects_committed((effect_left.effect_id, effect_right.effect_id))  # type: ignore[arg-type]
    assert retry_safe(tree.effects)
    with pytest.raises(ValueError):
        ToolEffectRecord(
            "bad",
            "write",
            ToolEffectKind.SIDE_EFFECTING,
            ToolEffectStatus.SUCCEEDED,
            content=ToolTextContent("bad"),
            certainty=EffectCertainty.UNKNOWN,
        )
    blocker = RetryBlocker(
        RetryBlockerCode.SETTLEMENT_UNCERTAIN, left.lineage.scope_id, "unknown"
    )
    assert not retry_safe(tree.effects, (blocker,))


def test_settlement_force_and_cleanup_lifo() -> None:
    async def check() -> None:
        tree = _tree()
        scope = tree.root_scope.child_tool(tool_call_id="tool")
        order: list[str] = []

        class Handler:
            async def settle(self):
                await asyncio.sleep(1)

            async def force_settle(self):
                order.append("forced")

        from roboagent.runtime import RuntimeToolExecutionContext

        execution = RuntimeToolExecutionContext(scope, object(), "session")
        async with execution.settlement_barrier(handler=Handler()):
            order.append("body")
        assert order == ["body", "forced"]

        class Resource:
            def __init__(self, name):
                self.name = name

            async def close(self):
                order.append(self.name)

            async def force_close(self):
                raise AssertionError("not forced")

        scope.register_resource(Resource("one"))
        scope.register_resource(Resource("two"))
        await tree.close()
        assert order[-2:] == ["two", "one"]

        failing = _tree()
        failing_scope = failing.root_scope.child_tool(tool_call_id="failure")

        class Broken:
            async def settle(self):
                raise RuntimeError("broken")

            async def force_settle(self):
                raise RuntimeError("still broken")

        failing_execution = RuntimeToolExecutionContext(failing_scope, object())
        with pytest.raises(SettlementError) as caught:
            async with failing_execution.settlement_barrier(handler=Broken()):
                pass
        assert caught.value.code == "settlement_failed"
        assert failing.retry_blockers[0].code is RetryBlockerCode.SETTLEMENT_UNCERTAIN

    asyncio.run(check())


def test_execution_record_bounds_overflow_and_supplemental_summary() -> None:
    tree = _tree(max_execution_records=2, max_record_evidence_bytes=64)
    scope = tree.root_scope.child_tool(tool_call_id="call")
    tree.add_tool_record(
        scope,
        tool_call_id="call",
        tool_name="tool",
        arguments=FrozenJsonObject({"secret": "x" * 100}),
        arguments_preview=FrozenJsonObject({"secret": "x" * 100}),
        status=ExecutionRecordStatus.SUCCEEDED,
        error_code=None,
        evidence=FrozenJsonObject({"data": "y" * 100}),
    )
    scope.contribute(
        ExecutionContribution(
            scope.next_contribution_id(),
            records=(SupplementalExecutionRecord(ExecutionRecordStatus.SUCCEEDED),),
        )
    )
    assert len(tree.execution_records) == 2
    assert tree.execution_records[0].arguments_preview is None
    assert (
        tree.execution_records[0].evidence is None
        or tree.execution_records[0].evidence.get("omitted") is True
    )
    assert tree.execution_records[-1].error_code == "execution_record_overflow"
    assert not tree.execution_records_complete


def test_runtime_context_event_lineage_usage_and_effect_commit_are_integrated() -> None:
    async def check() -> None:
        call = ToolCall("call", "work")

        class Model:
            capabilities = ModelCapabilities(tool_calling=True)

            def __init__(self):
                self.turn = 0

            async def stream(self, context, settings=None):
                self.turn += 1
                message = (
                    AssistantMessage(tool_calls=(call,))
                    if self.turn == 1
                    else AssistantMessage("done")
                )
                yield ResponseStarted(f"response-{self.turn}", 0)
                sequence = 1
                if message.tool_calls:
                    yield ToolCallStarted(sequence, 0, call.id, call.name)
                    sequence += 1
                    yield ToolCallCompleted(sequence, 0, call)
                    sequence += 1
                usage = Usage(self.turn, 1, self.turn + 1)
                yield UsageUpdated(sequence, usage)
                sequence += 1
                reason = (
                    FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP
                )
                yield ResponseCompleted(sequence, ModelResponse(message, reason, usage))

        captured = []

        class Hook:
            async def on_run_start(self, context):
                captured.append(context.run_context)

        tool = Tool(
            ToolDefinition("work", "Do work.", FrozenJsonObject({"type": "object"})),
            lambda arguments, context: ToolTextContent("done"),
            effect_kind=ToolEffectKind.SIDE_EFFECTING,
        )
        session = Agent(
            Model(), tool_registry=ToolRegistry((tool,)), hooks=(Hook(),)
        ).new_session()
        run = session.start(UserMessage("go"))
        subscription = run.subscribe()
        result = await run.result()
        events = [event async for event in subscription]

        assert captured[0].execution is not None
        assert result.usage == Usage(3, 2, 5) and result.usage_known is True
        assert len(result.effects) == 1 and result.effects[0].transcript_committed
        assert len(result.execution_records) == 1
        assert [event.sequence for event in events] == list(range(len(events)))
        assert all(event.run_id == result.run_id for event in events)
        assert all(event.lineage is not None for event in events)
        tool_event = next(event for event in events if event.type == "tool.started")
        assert tool_event.lineage.scope_depth == 1
        assert tool_event.lineage.tool_call_id == "call"

    asyncio.run(check())


def test_child_events_share_root_sequence_and_are_not_terminal() -> None:
    async def check() -> None:
        tree = _tree()
        child = tree._new_scope(
            execution_run_id="child",
            parent=tree.root_scope,
            agent_depth=1,
            cancellation=tree.cancellation,
            deadline=None,
            agent_tool_name="delegate",
        )
        events = RunEventEmitter(
            "root", execution_tree=tree, lineage=tree.root_scope.lineage
        )
        started = await events.emit("run.started")
        completed = await events.emit("child_run.completed", lineage=child.lineage)
        model = await events.emit("model.started")
        assert [started.sequence, completed.sequence, model.sequence] == [0, 1, 2]
        assert completed.run_id == "root"
        assert completed.lineage.execution_run_id == "child"

    asyncio.run(check())


def test_task_cancellation_does_not_interrupt_settlement() -> None:
    async def check() -> None:
        tree = _tree(settlement_timeout=1)
        scope = tree.root_scope.child_tool(tool_call_id="tool")
        entered = asyncio.Event()
        release = asyncio.Event()
        settled: list[bool] = []

        class Handler:
            async def settle(self):
                entered.set()
                await release.wait()
                settled.append(True)

            async def force_settle(self):
                raise AssertionError("force should not run")

        from roboagent.runtime import RuntimeToolExecutionContext

        async def invoke():
            async with RuntimeToolExecutionContext(scope, object()).settlement_barrier(
                handler=Handler()
            ):
                pass

        task = asyncio.create_task(invoke())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settled == [True]

    asyncio.run(check())


def test_execution_tree_does_not_freeze_scope_during_active_settlement() -> None:
    async def check() -> None:
        tree = _tree(settlement_timeout=1)
        scope = tree.root_scope.child_tool(tool_call_id="tool")
        entered = asyncio.Event()
        release = asyncio.Event()

        class Handler:
            async def settle(self):
                entered.set()
                await release.wait()

            async def force_settle(self):
                raise AssertionError("force should not run")

        from roboagent.runtime import RuntimeToolExecutionContext

        async def invoke():
            async with RuntimeToolExecutionContext(scope, object()).settlement_barrier(
                handler=Handler()
            ):
                pass

        invocation = asyncio.create_task(invoke())
        await entered.wait()
        closing = asyncio.create_task(tree.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await invocation
        await closing
        assert scope.state is ExecutionScopeState.FROZEN

    asyncio.run(check())
