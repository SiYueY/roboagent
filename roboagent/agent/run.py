"""Eager V1 run with safe public event replay and cooperative cancellation."""

from __future__ import annotations
import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator
from uuid import uuid4
from roboagent.agent.loop import MaxTurnsError, run_loop
from roboagent.agent.types import RunConfig, RunResult
from roboagent.agent.types import PendingControl
from roboagent.message import UserMessage
from roboagent.runtime.event import AgentEvent
from roboagent.runtime.types import (
    CancellationReason,
    RunContext,
    RunError,
    RunPhase,
    RunState,
    RunStatus,
    RunTerminationReason,
    RuntimeCancellation,
    ContextPreparationError,
    MediaResolutionError,
    ModelCapabilityError,
    ModelProtocolError,
)

if TYPE_CHECKING:
    from roboagent.agent.session import AgentSession


class RunFinishedError(RuntimeError):
    pass


_END = object()
_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentRun:
    session: AgentSession
    config: RunConfig
    metadata: dict[str, object]
    run_id: str = field(default_factory=lambda: uuid4().hex)
    _token: RuntimeCancellation = field(default_factory=RuntimeCancellation, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _result: asyncio.Future[RunResult] | None = field(default=None, init=False)
    _history: list[AgentEvent] = field(default_factory=list, init=False)
    _queues: set[asyncio.Queue[AgentEvent | object]] = field(
        default_factory=set, init=False
    )
    _sequence: int = field(default=0, init=False)
    _state: RunState = field(
        default_factory=lambda: RunState(RunStatus.CREATED, RunPhase.IDLE, 0),
        init=False,
    )
    _controls: list[PendingControl] = field(default_factory=list, init=False)
    _control_sequence: int = field(default=0, init=False)
    _control_signal: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _terminalizing: bool = field(default=False, init=False)

    @property
    def state(self) -> RunState:
        return self._state

    def _set_phase(self, phase: RunPhase, turn: int, **kwargs: object) -> None:
        """Publish a media-safe snapshot; canonical messages stay in Session."""
        self._state = RunState(RunStatus.RUNNING, phase, turn, **kwargs)

    def start_eager(self) -> None:
        if self._task is None:
            loop = asyncio.get_running_loop()
            self._result = loop.create_future()
            self._task = loop.create_task(self._execute())

    def cancel(self) -> None:
        if not self._terminalizing and not self._token.cancelled:
            self._token.cancel(CancellationReason.USER)
            asyncio.create_task(
                self._emit(
                    "cancellation_requested", status=CancellationReason.USER.value
                )
            )

    def steer(self, message: str | UserMessage) -> None:
        self._receive_control("steer", message)

    def follow_up(self, message: str | UserMessage) -> None:
        self._receive_control("follow_up", message)

    def _receive_control(self, kind: str, message: str | UserMessage) -> None:
        if self._terminalizing or self._result is not None and self._result.done():
            raise RunFinishedError("Run is already terminal.")
        user = (
            message
            if isinstance(message, UserMessage)
            else UserMessage(message, limits=self.session._media_limits)
        )
        self._control_sequence += 1
        self._controls.append(PendingControl(self._control_sequence, kind, user))
        self._control_signal.set()
        # Events are safe: only type and text are exposed, not raw media.
        asyncio.create_task(self._emit(kind + "_received", text=None))

    def consume_controls(self) -> tuple[PendingControl, ...]:
        controls = tuple(self._controls)
        self._controls.clear()
        return controls

    def observe_controls(self) -> tuple[PendingControl, ...]:
        """Expose pending controls to scheduling without committing them."""
        return tuple(self._controls)

    async def wait_for_control(self, after_sequence: int) -> int:
        """Wait for a control newer than ``after_sequence`` without consuming it."""
        while self._control_sequence <= after_sequence:
            await self._control_signal.wait()
            self._control_signal.clear()
        return self._control_sequence

    async def result(self) -> RunResult:
        assert self._result is not None
        return await asyncio.shield(self._result)

    def events(self) -> AsyncIterator[AgentEvent]:
        # Replay is part of the public contract: a terminal subscriber must
        # receive the whole retained history, while a live slow subscriber is
        # still disconnected after its own 128-event backlog.
        queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue(
            len(self._history) + 129
        )
        # Snapshot and register run synchronously on the event loop: no replay/live gap.
        for item in self._history:
            queue.put_nowait(item)
        if self._task is None or self._task.done():
            queue.put_nowait(_END)
        else:
            self._queues.add(queue)

        async def iterate() -> AsyncIterator[AgentEvent]:
            try:
                while (item := await queue.get()) is not _END:
                    yield item  # type: ignore[misc]
            finally:
                self._queues.discard(queue)

        return iterate()

    async def _emit(self, type: str, **kwargs: object) -> None:
        self._sequence += 1
        event = AgentEvent(self.run_id, self._sequence, type, **kwargs)
        self._history.append(event)
        for queue in tuple(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A disconnected slow subscriber must eventually terminate its
                # iterator instead of waiting forever after its backlog drains.
                self._queues.discard(queue)
                try:
                    queue.get_nowait()
                    queue.put_nowait(_END)
                except asyncio.QueueEmpty:
                    pass

    async def _hook(self, name: str, *args: object) -> None:
        hooks = self.session.agent.hooks
        callback = getattr(hooks, name, None) if hooks is not None else None
        if callback is None:
            return
        try:
            value = callback(*args)
            if inspect.isawaitable(value):
                await value
        except Exception as exc:
            # Do not log hook arguments: they may include transcript media.
            _LOG.warning(
                "RoboAgent lifecycle hook %s failed (%s)", name, type(exc).__name__
            )

    async def _execute(self) -> None:
        self._state = RunState(RunStatus.RUNNING, RunPhase.IDLE, 0)
        await self._emit("run_started")
        reason = RunTerminationReason.RUNTIME_ERROR
        error = None
        final = None
        turns = 0
        timeout = asyncio.create_task(self._timeout()) if self.config.timeout else None
        try:
            context = RunContext(
                self.session.session_id,
                self.run_id,
                self._token,
                metadata=self.metadata,
            )
            await self._hook("on_run_start", context)
            final, turns = await run_loop(
                agent=self.session.agent,
                session=self.session,
                run_context=context,
                config=self.config,
                emit=self._emit,
                consume_controls=self.consume_controls,
                observe_controls=self.observe_controls,
                wait_for_control=self.wait_for_control,
                update_state=self._set_phase,
                hook=self._hook,
            )
            reason = RunTerminationReason.COMPLETED
        except MaxTurnsError as exc:
            turns = exc.turns
            reason = RunTerminationReason.MAX_TURNS
            error = RunError(reason.value, "Run reached its configured limit.")
        except TimeoutError:
            turns = self._state.turn
            reason = RunTerminationReason.TIMED_OUT
            error = RunError(reason.value, "Run reached its configured limit.")
        except asyncio.CancelledError:
            turns = self._state.turn
            self._token.cancel(CancellationReason.RUN_TERMINATED)
            reason = (
                RunTerminationReason.TIMED_OUT
                if self._token.reason is CancellationReason.TIMEOUT
                else RunTerminationReason.CANCELLED
            )
        except Exception as exc:
            turns = self._state.turn
            if self._token.reason is CancellationReason.TIMEOUT:
                reason = RunTerminationReason.TIMED_OUT
            elif self._token.cancelled:
                reason = RunTerminationReason.CANCELLED
            elif str(exc) == "tool_policy_fail_run":
                reason = RunTerminationReason.TOOL_ERROR
            elif isinstance(exc, ContextPreparationError):
                reason = RunTerminationReason.CONTEXT_ERROR
            elif isinstance(exc, MediaResolutionError):
                reason = RunTerminationReason.MODEL_ERROR
            else:
                reason = RunTerminationReason.MODEL_ERROR
            code = (
                exc.code.value
                if isinstance(exc, MediaResolutionError)
                else exc.code
                if isinstance(exc, (ModelCapabilityError, ModelProtocolError))
                else reason.value
            )
            error = RunError(
                code, "Run execution failed.", cause_type=type(exc).__name__
            )
        finally:
            if timeout:
                timeout.cancel()
        # Once the loop has stopped, no Model/Tool work is in flight.  This is
        # the final safe boundary mandated for every normal terminal outcome.
        self._terminalizing = True
        uncommitted: tuple[PendingControl, ...] = ()
        terminal_controls = self.consume_controls()
        try:
            for index, control in enumerate(terminal_controls):
                self.session._append(control.message)
        except Exception as exc:
            uncommitted = terminal_controls[index:]
            reason = RunTerminationReason.INVALID_STATE
            error = RunError(
                reason.value,
                "Could not commit pending controls.",
                cause_type=type(exc).__name__,
            )
        status = {
            RunTerminationReason.COMPLETED: RunStatus.COMPLETED,
            RunTerminationReason.CANCELLED: RunStatus.CANCELLED,
            RunTerminationReason.TIMED_OUT: RunStatus.TIMED_OUT,
            RunTerminationReason.MAX_TURNS: RunStatus.MAX_TURNS,
        }.get(reason, RunStatus.FAILED)
        try:
            self.session._finish(self)
        except Exception as exc:
            status = RunStatus.FAILED
            reason = RunTerminationReason.INVALID_STATE
            error = RunError(
                reason.value,
                "Canonical transcript validation failed.",
                cause_type=type(exc).__name__,
            )
        self._state = RunState(status, RunPhase.TERMINAL, turns, error=error)
        await self._emit(
            "run_" + status.value,
            turn=turns,
            status=status.value,
            error_code=error.code if error else None,
            error=error.message if error else None,
        )
        assert self._result is not None
        result = RunResult(status, final, turns, reason, error, uncommitted)
        await self._hook("on_run_end", result)
        self._result.set_result(result)
        for queue in tuple(self._queues):
            try:
                queue.put_nowait(_END)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(_END)
                except asyncio.QueueEmpty:
                    pass
        self._queues.clear()

    async def _timeout(self) -> None:
        assert self.config.timeout is not None
        await asyncio.sleep(self.config.timeout)
        if not self._token.cancelled:
            self._token.cancel(CancellationReason.TIMEOUT)
            await self._emit(
                "cancellation_requested", status=CancellationReason.TIMEOUT.value
            )
