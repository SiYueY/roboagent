"""V1 tool batch executor with policy-aware, stable transcript ordering."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Mapping

from roboagent.agent.types import ToolExecutionConfig, ToolExecutionMode
from roboagent.message import (
    MediaLimits,
    TextContent,
    ToolCall,
    ToolCallStatus,
    ToolExecutionError,
    ToolResultMessage,
)
from roboagent.runtime.types import (
    CancellationReason,
    ContentSummary,
    ModelContext,
    RunContext,
    RuntimeCancellation,
    content_summary,
)
from roboagent.tool import Tool, ToolCallContext, ToolInvocation, ToolOutput
from roboagent.tool.tool import InvalidToolOutputError


@dataclass(frozen=True, slots=True)
class ToolCallOutcome:
    call_id: str
    tool_name: str
    status: ToolCallStatus
    output: ToolOutput | None = None
    error: ToolExecutionError | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionBatchResult:
    outcomes: tuple[ToolCallOutcome, ...]
    fail_run: bool = False


class BeforeToolAction(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    SKIP = "skip"
    FAIL_RUN = "fail_run"


class ToolErrorAction(Enum):
    CONTINUE = "continue"
    STOP_BATCH = "stop_batch"
    FAIL_RUN = "fail_run"


class SteeringAction(Enum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    SKIP = "skip"


class ToolCallState(Enum):
    PENDING = "pending"
    RUNNING = "running"


class ToolExecutionPolicy:
    """Per-run policy. Its decisions are applied by the executor, never tools."""

    def before_call(self, call: ToolCall) -> BeforeToolAction:
        return BeforeToolAction.ALLOW

    def on_error(self, outcome: ToolCallOutcome) -> ToolErrorAction:
        return ToolErrorAction.STOP_BATCH

    def on_steer(self, state: ToolCallState) -> SteeringAction:
        return (
            SteeringAction.SKIP
            if state is ToolCallState.PENDING
            else SteeringAction.CONTINUE
        )


class DefaultToolExecutionPolicy(ToolExecutionPolicy):
    pass


class ToolExecutor:
    def __init__(
        self,
        tools: Mapping[str, Tool],
        limits: MediaLimits,
        config: ToolExecutionConfig,
        policy: ToolExecutionPolicy | None = None,
        emit: Callable[..., object] | None = None,
        hook: Callable[..., object] | None = None,
        observe_controls: Callable[[], tuple[object, ...]] | None = None,
        wait_for_control: Callable[[int], Awaitable[int]] | None = None,
    ) -> None:
        self.tools = tools
        self.limits = limits
        self.config = config
        self.policy = policy or DefaultToolExecutionPolicy()
        self.emit = emit
        self.hook = hook
        self.observe_controls = observe_controls
        self.wait_for_control = wait_for_control

    @staticmethod
    def _skipped(call: ToolCall, code: str = "batch_stopped") -> ToolCallOutcome:
        return ToolCallOutcome(
            call.id,
            call.name,
            ToolCallStatus.SKIPPED,
            error=ToolExecutionError(code, "Tool batch stopped."),
        )

    @staticmethod
    def _cancelled(call: ToolCall) -> ToolCallOutcome:
        return ToolCallOutcome(
            call.id,
            call.name,
            ToolCallStatus.CANCELLED,
            error=ToolExecutionError("cancelled", "Tool execution cancelled."),
        )

    async def execute(
        self,
        calls: tuple[ToolCall, ...],
        run_context: RunContext,
        model_context: ModelContext,
    ) -> ToolExecutionBatchResult:
        reported_terminal_ids: set[str] = set()

        async def report_start(call: ToolCall) -> None:
            if self.hook is not None:
                value = self.hook("on_tool_start", call)
                if inspect.isawaitable(value):
                    await value
            if self.emit is None:
                return
            value = self.emit(
                "tool_started",
                turn=run_context.turn,
                tool_call_id=call.id,
                tool_name=call.name,
            )
            if inspect.isawaitable(value):
                await value

        async def report_terminal(outcome: ToolCallOutcome) -> None:
            if outcome.call_id in reported_terminal_ids:
                return
            reported_terminal_ids.add(outcome.call_id)
            if self.hook is not None:
                value = self.hook("on_tool_end", outcome)
                if inspect.isawaitable(value):
                    await value
            if self.emit is None:
                return
            content: tuple[ContentSummary, ...] = ()
            if outcome.output is not None:
                content = tuple(
                    content_summary(part) for part in outcome.output.content
                )
            event_type = {
                ToolCallStatus.COMPLETED: "tool_completed",
                ToolCallStatus.FAILED: "tool_failed",
                ToolCallStatus.CANCELLED: "tool_cancelled",
                ToolCallStatus.SKIPPED: "tool_skipped",
            }[outcome.status]
            value = self.emit(
                event_type,
                turn=run_context.turn,
                tool_call_id=outcome.call_id,
                tool_name=outcome.tool_name,
                status=outcome.status.value,
                content=content,
                error_code=outcome.error.code if outcome.error else None,
            )
            if inspect.isawaitable(value):
                await value

        async def one(call: ToolCall, child: RuntimeCancellation) -> ToolCallOutcome:
            try:
                action = self.policy.before_call(call)
            except Exception:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError(
                        "policy_exception", "Tool policy failed before execution."
                    ),
                )
            if action is BeforeToolAction.SKIP:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.SKIPPED,
                    error=ToolExecutionError(
                        "policy_skipped", "Tool skipped by policy."
                    ),
                )
            if action in {BeforeToolAction.REJECT, BeforeToolAction.FAIL_RUN}:
                code = (
                    "policy_fail_run"
                    if action is BeforeToolAction.FAIL_RUN
                    else "policy_denied"
                )
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError(code, "Tool rejected by policy."),
                )
            if child.cancelled:
                return self._cancelled(call)
            tool = self.tools.get(call.name)
            if tool is None:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError("unknown_tool", "Unknown tool."),
                )
            if call.parse_error or call.arguments is None:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError(
                        "invalid_arguments", "Invalid tool arguments."
                    ),
                )
            params = tool.validate(call.arguments)
            if isinstance(params, ToolExecutionError):
                return ToolCallOutcome(
                    call.id, call.name, ToolCallStatus.FAILED, error=params
                )
            try:
                await report_start(call)
                invocation_context = (
                    model_context if tool.expose_model_context else None
                )
                task = asyncio.create_task(
                    tool.invoke(
                        params,
                        ToolInvocation(
                            call,
                            run_context,
                            ToolCallContext(call.id, child),
                            invocation_context,
                        ),
                        limits=self.limits,
                    )
                )
                cancelled = asyncio.create_task(child.wait_cancelled())
                done, pending = await asyncio.wait(
                    (task, cancelled), return_when=asyncio.FIRST_COMPLETED
                )
                for pending_task in pending:
                    pending_task.cancel()
                if cancelled in done:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return self._cancelled(call)
                output = task.result()
                if output.is_error:
                    return ToolCallOutcome(
                        call.id,
                        call.name,
                        ToolCallStatus.FAILED,
                        output,
                        ToolExecutionError(
                            output.error_code or "tool_error", "Tool returned an error."
                        ),
                    )
                return ToolCallOutcome(
                    call.id, call.name, ToolCallStatus.COMPLETED, output
                )
            except asyncio.CancelledError:
                return self._cancelled(call)
            except InvalidToolOutputError:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError(
                        "invalid_tool_output", "Tool returned invalid output."
                    ),
                )
            except Exception:
                return ToolCallOutcome(
                    call.id,
                    call.name,
                    ToolCallStatus.FAILED,
                    error=ToolExecutionError(
                        "execution_error", "Tool execution failed."
                    ),
                )

        def error_decision(outcome: ToolCallOutcome) -> ToolErrorAction:
            if outcome.error and outcome.error.code == "policy_fail_run":
                return ToolErrorAction.FAIL_RUN
            try:
                return self.policy.on_error(outcome)
            except Exception:
                return ToolErrorAction.FAIL_RUN

        if self.config.mode is ToolExecutionMode.SEQUENTIAL:
            built: list[ToolCallOutcome] = []
            stop = False
            fail_run = False
            observed_control = 0
            skip_remaining_for_steer = False

            def new_steer_actions(state: ToolCallState) -> tuple[SteeringAction, ...]:
                nonlocal observed_control
                if self.observe_controls is None:
                    return ()
                actions: list[SteeringAction] = []
                for control in sorted(
                    self.observe_controls(),
                    key=lambda item: getattr(item, "sequence", 0),
                ):
                    sequence = getattr(control, "sequence", 0)
                    if sequence <= observed_control:
                        continue
                    observed_control = sequence
                    if getattr(control, "kind", None) == "steer":
                        actions.append(self.policy.on_steer(state))
                return tuple(actions)

            async def execute_one(call: ToolCall) -> tuple[ToolCallOutcome, bool]:
                """Run one call while still allowing a policy to cancel it."""
                nonlocal skip_remaining_for_steer
                child = run_context.cancellation.child()
                task = asyncio.create_task(one(call, child))
                invalid_steering = False
                while not task.done() and self.wait_for_control is not None:
                    signal = asyncio.create_task(
                        self.wait_for_control(observed_control)
                    )
                    done, _ = await asyncio.wait(
                        (task, signal), return_when=asyncio.FIRST_COMPLETED
                    )
                    if task in done:
                        signal.cancel()
                        await asyncio.gather(signal, return_exceptions=True)
                        break
                    await signal
                    try:
                        actions = new_steer_actions(ToolCallState.RUNNING)
                        # A steer observed while a sequential call is running
                        # also has a policy decision for calls still pending.
                        pending_actions = tuple(
                            self.policy.on_steer(ToolCallState.PENDING) for _ in actions
                        )
                    except Exception:
                        child.cancel(CancellationReason.TOOL_POLICY)
                        invalid_steering = True
                        break
                    if SteeringAction.SKIP in actions:
                        child.cancel(CancellationReason.TOOL_POLICY)
                        invalid_steering = True
                        break
                    if SteeringAction.CANCEL in actions:
                        child.cancel(CancellationReason.TOOL_POLICY)
                    if any(
                        action in {SteeringAction.SKIP, SteeringAction.CANCEL}
                        for action in pending_actions
                    ):
                        skip_remaining_for_steer = True
                outcome = await task
                return outcome, invalid_steering

            for call in calls:
                if stop or run_context.cancellation.cancelled:
                    outcome = self._skipped(
                        call,
                        "cancelled_before_start"
                        if run_context.cancellation.cancelled
                        else "batch_stopped",
                    )
                    built.append(outcome)
                    await report_terminal(outcome)
                    continue
                try:
                    pending_actions = new_steer_actions(ToolCallState.PENDING)
                except Exception:
                    outcome = ToolCallOutcome(
                        call.id,
                        call.name,
                        ToolCallStatus.FAILED,
                        error=ToolExecutionError(
                            "policy_exception",
                            "Tool policy failed while handling steering.",
                        ),
                    )
                    built.append(outcome)
                    await report_terminal(outcome)
                    stop = fail_run = True
                    continue
                if skip_remaining_for_steer or any(
                    action in {SteeringAction.SKIP, SteeringAction.CANCEL}
                    for action in pending_actions
                ):
                    outcome = self._skipped(call, "steering_skipped")
                    built.append(outcome)
                    await report_terminal(outcome)
                    continue
                outcome, invalid_steering = await execute_one(call)
                built.append(outcome)
                await report_terminal(outcome)
                if invalid_steering:
                    stop = fail_run = True
                    continue
                if outcome.status is ToolCallStatus.FAILED:
                    decision = error_decision(outcome)
                    stop = decision is not ToolErrorAction.CONTINUE
                    fail_run = decision is ToolErrorAction.FAIL_RUN
            return ToolExecutionBatchResult(tuple(built), fail_run)

        # A bounded scheduler makes policy semantics deterministic: STOP_BATCH
        # skips pending calls, while FAIL_RUN also cancels running siblings.
        built: list[ToolCallOutcome | None] = [None] * len(calls)
        capacity = self.config.max_concurrency or len(calls) or 1
        next_index = 0
        stop = False
        fail_run = False
        observed_control = 0
        running: dict[
            asyncio.Task[ToolCallOutcome], tuple[int, RuntimeCancellation]
        ] = {}

        def start(index: int) -> None:
            child = run_context.cancellation.child()
            task = asyncio.create_task(one(calls[index], child))
            running[task] = (index, child)

        def fill() -> None:
            nonlocal next_index
            if run_context.cancellation.cancelled:
                while next_index < len(calls):
                    if built[next_index] is None:
                        built[next_index] = self._skipped(
                            calls[next_index], "cancelled_before_start"
                        )
                    next_index += 1
                return
            while next_index < len(calls) and built[next_index] is not None:
                next_index += 1
            while not stop and next_index < len(calls) and len(running) < capacity:
                start(next_index)
                next_index += 1
                while next_index < len(calls) and built[next_index] is not None:
                    next_index += 1

        def cancel_running() -> None:
            for _task, (_index, child) in running.items():
                child.cancel(CancellationReason.TOOL_POLICY)

        def apply_steering() -> None:
            """Observe controls without consuming them or changing transcript state."""
            nonlocal observed_control, stop, fail_run
            if self.observe_controls is None:
                return
            controls = sorted(
                self.observe_controls(),
                key=lambda control: getattr(control, "sequence", 0),
            )
            for control in controls:
                sequence = getattr(control, "sequence", 0)
                if sequence <= observed_control:
                    continue
                observed_control = sequence
                if getattr(control, "kind", None) != "steer":
                    continue
                for index in range(next_index, len(calls)):
                    if built[index] is not None:
                        continue
                    try:
                        action = self.policy.on_steer(ToolCallState.PENDING)
                    except Exception:
                        stop = fail_run = True
                        cancel_running()
                        return
                    if action in {SteeringAction.SKIP, SteeringAction.CANCEL}:
                        built[index] = self._skipped(calls[index], "steering_skipped")
                for _task, (_index, child) in running.items():
                    try:
                        action = self.policy.on_steer(ToolCallState.RUNNING)
                    except Exception:
                        stop = fail_run = True
                        cancel_running()
                        return
                    if action is SteeringAction.SKIP:
                        # The public policy contract declares SKIP invalid for a
                        # running call; terminate instead of silently guessing.
                        stop = fail_run = True
                        cancel_running()
                        return
                    if action is SteeringAction.CANCEL:
                        child.cancel(CancellationReason.TOOL_POLICY)

        apply_steering()
        fill()
        while running:
            control_wait: asyncio.Task[int] | None = None
            wait_set: set[asyncio.Task[object]] = set(running)  # type: ignore[arg-type]
            if self.wait_for_control is not None:
                control_wait = asyncio.create_task(
                    self.wait_for_control(observed_control)
                )
                wait_set.add(control_wait)  # type: ignore[arg-type]
            completed, _ = await asyncio.wait(
                wait_set, return_when=asyncio.FIRST_COMPLETED
            )
            if control_wait is not None:
                if control_wait in completed:
                    await control_wait
                    apply_steering()
                    completed.remove(control_wait)
                else:
                    control_wait.cancel()
                    await asyncio.gather(control_wait, return_exceptions=True)
            for task in completed:
                if task not in running:
                    continue
                index, _child = running.pop(task)
                try:
                    outcome = task.result()
                except asyncio.CancelledError:
                    outcome = self._cancelled(calls[index])
                built[index] = outcome
                await report_terminal(outcome)
                if outcome.status is ToolCallStatus.FAILED:
                    decision = error_decision(outcome)
                    if decision is not ToolErrorAction.CONTINUE:
                        stop = True
                    if decision is ToolErrorAction.FAIL_RUN:
                        fail_run = True
                        cancel_running()
            fill()

        for index in range(next_index, len(calls)):
            built[index] = self._skipped(
                calls[index],
                "cancelled_before_start"
                if run_context.cancellation.cancelled
                else "batch_stopped",
            )
            await report_terminal(built[index])
        for outcome in built:
            if outcome is not None:
                await report_terminal(outcome)
        return ToolExecutionBatchResult(
            tuple(outcome for outcome in built if outcome is not None), fail_run
        )


def tool_result(
    outcome: ToolCallOutcome, *, limits: MediaLimits | None = None
) -> ToolResultMessage:
    options = {"limits": limits} if limits is not None else {}
    if outcome.status is ToolCallStatus.COMPLETED:
        assert outcome.output is not None
        return ToolResultMessage(
            outcome.call_id,
            outcome.tool_name,
            outcome.status,
            outcome.output.content,
            **options,
        )
    message = outcome.error.message if outcome.error else outcome.status.value
    return ToolResultMessage(
        outcome.call_id,
        outcome.tool_name,
        outcome.status,
        (TextContent(message),),
        outcome.error,
        **options,
    )
