"""One eager, cancellable RoboAgent execution instance."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from uuid import uuid4

from roboagent.agent.hooks import RunEndHookContext, RunHookContext
from roboagent.agent.loop import MaxTurnsError, RunCancelled, _RunFailure, run_loop
from roboagent.agent.types import RunConfig, RunResult
from roboagent.runtime.event import EventSubscription, EventSubscriptionConfig, RunEventEmitter
from roboagent.runtime.types import (
    CancellationReason,
    RunContext,
    RunError,
    RunPhase,
    RunState,
    RunStatus,
    RuntimeCancellation,
)
from roboagent.tool import ToolEffectRecord, ToolExecutor, retry_safe

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roboagent.agent.session import Session


@dataclass(slots=True)
class Run:
    session: "Session"
    config: RunConfig
    run_id: str = field(default_factory=lambda: uuid4().hex)
    _cancellation: RuntimeCancellation = field(default_factory=RuntimeCancellation, init=False, repr=False)
    _events: RunEventEmitter = field(init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _result: asyncio.Future[RunResult] | None = field(default=None, init=False, repr=False)
    _state: RunState = field(default_factory=lambda: RunState(RunPhase.IDLE, 0), init=False, repr=False)
    _skill_catalog: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._events = RunEventEmitter(self.run_id)

    @property
    def state(self) -> RunState:
        return self._state

    def start_eager(self) -> None:
        if self._task is not None:
            raise RuntimeError("Run already started.")
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        self._task = loop.create_task(self._execute())

    def cancel(self) -> None:
        self._cancellation.cancel(CancellationReason.USER)

    async def result(self) -> RunResult:
        if self._result is None:
            raise RuntimeError("Run has not started.")
        return await asyncio.shield(self._result)

    def subscribe(self, config: EventSubscriptionConfig | None = None) -> EventSubscription:
        return self._events.subscribe(config)

    def _set_state(self, phase: RunPhase, turn: int, **values: object) -> None:
        self._state = RunState(phase, turn, **values)

    async def _execute(self) -> None:
        context = RunContext(self.run_id, self.session.session_id, self._cancellation)
        status = RunStatus.FAILED
        output = None
        usage = None
        error: RunError | None = None
        cleanup_errors: list[RunError] = []
        effects: tuple[ToolEffectRecord, ...] = ()
        turns = 0
        timeout_task = asyncio.create_task(self._timeout()) if self.config.timeout else None
        manager = self.session.agent.skill_manager
        try:
            await self._events.emit("run.started")
            await self._invoke_hooks("on_run_start", RunHookContext(context))
            tool_executor = ToolExecutor(
                registry=self.session.agent.tool_registry,
                policy=self.session.agent.tool_policy,
                hooks=self.session.agent.hooks,
                events=self._events,
                config=self.config.tool_executor,
                hook_timeout=self.config.hook_timeout,
            )
            outcome = await run_loop(
                agent=self.session.agent,
                session=self.session,
                run_context=context,
                config=self.config,
                events=self._events,
                invoke_hooks=self._invoke_hooks,
                update_state=self._set_state,
                guidance_metadata=tuple(getattr(self._skill_catalog, "metadata", ())),
                tool_executor=tool_executor,
            )
            status = RunStatus.COMPLETED
            output, usage, effects, turns = outcome.output, outcome.usage, outcome.effects, outcome.turns
        except MaxTurnsError as exc:
            status = RunStatus.FAILED
            error = RunError("max_turns", "Run reached its maximum model turns.")
            output, usage, effects, turns = (
                exc.outcome.output,
                exc.outcome.usage,
                exc.outcome.effects,
                exc.outcome.turns,
            )
        except _RunFailure as exc:
            status = RunStatus.FAILED
            error = exc.error
            effects = exc.effects
            output, usage, turns = exc.output, exc.usage, exc.turns or self._state.turn
        except RunCancelled as exc:
            effects = exc.effects
            output, usage, turns = exc.output, exc.usage, exc.turns or self._state.turn
            if self._cancellation.reason is CancellationReason.TIMEOUT:
                status = RunStatus.FAILED
                error = RunError("timeout", "Run timed out.")
            else:
                status = RunStatus.CANCELLED
        except asyncio.CancelledError:
            turns = self._state.turn
            if self._cancellation.reason is CancellationReason.TIMEOUT:
                status = RunStatus.FAILED
                error = RunError("timeout", "Run timed out.")
            else:
                status = RunStatus.CANCELLED
        except Exception as exc:
            status = RunStatus.FAILED
            error = RunError("runtime_error", "Run execution failed.", cause_type=type(exc).__name__)
            turns = self._state.turn
        finally:
            if timeout_task is not None:
                timeout_task.cancel()
                await asyncio.gather(timeout_task, return_exceptions=True)

        provisional = status
        end_context = RunEndHookContext(context, provisional, error, effects)
        for hook in self.session.agent.hooks:
            callback = getattr(hook, "on_run_end", None)
            if callback is None:
                continue
            try:
                value = callback(end_context)
                if not inspect.isawaitable(value):
                    raise TypeError("on_run_end hook must be async.")
                if self.config.cleanup_hook_timeout is None:
                    hook_result = await value
                else:
                    hook_result = await asyncio.wait_for(value, self.config.cleanup_hook_timeout)
                if hook_result is not None:
                    raise TypeError("on_run_end hook must return None.")
            except BaseException as exc:
                cleanup = RunError("cleanup_hook_error", "on_run_end hook failed.", cause_type=type(exc).__name__)
                if provisional is RunStatus.COMPLETED and error is None:
                    status = RunStatus.FAILED
                    error = cleanup
                else:
                    cleanup_errors.append(cleanup)
        if manager is not None:
            try:
                manager.release_run(self.run_id)
            except Exception as exc:
                release_error = RunError("cleanup_error", "Skill catalog cleanup failed.", cause_type=type(exc).__name__)
                if status is RunStatus.COMPLETED and error is None:
                    status = RunStatus.FAILED
                    error = release_error
                else:
                    cleanup_errors.append(release_error)
        try:
            await self.session.release_run(self.run_id)
        except Exception as exc:
            release_error = RunError("cleanup_error", "Session ownership cleanup failed.", cause_type=type(exc).__name__)
            if status is RunStatus.COMPLETED and error is None:
                status = RunStatus.FAILED
                error = release_error
            else:
                cleanup_errors.append(release_error)
        self._state = RunState(RunPhase.TERMINAL, turns, status=status, error=error)
        result = RunResult(
            self.run_id,
            status,
            output,
            usage,
            error,
            tuple(cleanup_errors),
            effects,
            retry_safe(effects),
        )
        terminal = {
            RunStatus.COMPLETED: "run.completed",
            RunStatus.FAILED: "run.failed",
            RunStatus.CANCELLED: "run.cancelled",
        }[status]
        await self._events.emit(terminal, status=status.value, error_code=error.code if error else None)
        assert self._result is not None
        self._result.set_result(result)

    async def _invoke_hooks(self, name: str, *args: object) -> tuple[object, ...]:
        results: list[object] = []
        for hook in self.session.agent.hooks:
            callback = getattr(hook, name, None)
            if callback is None:
                continue
            try:
                value = callback(*args)
                if not inspect.isawaitable(value):
                    raise TypeError(f"{name} hook must be async.")
                value = await self._await_hook(value)
                if name in {"on_run_start", "after_model"} and value is not None:
                    raise TypeError(f"{name} hook must return None.")
                results.append(value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _RunFailure(RunError("hook_error", f"{name} hook failed.", cause_type=type(exc).__name__)) from exc
        return tuple(results)

    async def _await_hook(self, value: object) -> object:
        task = asyncio.ensure_future(value)  # type: ignore[arg-type]
        cancelled = asyncio.create_task(self._cancellation.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {task, cancelled},
                timeout=self.config.hook_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError()
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._cancellation.raise_if_cancelled()
                raise TimeoutError("Hook timed out.")
            return task.result()
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _timeout(self) -> None:
        assert self.config.timeout is not None
        await asyncio.sleep(self.config.timeout)
        self._cancellation.cancel(CancellationReason.TIMEOUT)
