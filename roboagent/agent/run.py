"""One eager, cancellable RoboAgent execution instance."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from uuid import uuid4

from roboagent.agent.hooks import RunEndHookContext, RunHookContext
from roboagent.agent.loop import MaxTurnsError, RunCancelled, _RunFailure, run_loop
from roboagent.agent.types import RunConfig, RunResult
from roboagent.runtime.event import (
    EventSubscription,
    EventSubscriptionConfig,
    RunEventEmitter,
)
from roboagent.runtime.types import (
    CancellationReason,
    RunContext,
    RunError,
    RunPhase,
    RunState,
    RunStatus,
    RuntimeCancellation,
    ToolCallSummary,
)
from roboagent.runtime.execution import (
    CleanupError,
    ExecutionTree,
    ExecutionScope,
    ExecutionRequestError,
    RuntimeRunExecutionContext,
    absolute_deadline,
)
from roboagent.tool import ToolEffectRecord, ToolExecutor, retry_safe

from typing import TYPE_CHECKING, Awaitable, Callable, cast

if TYPE_CHECKING:
    from roboagent.message import AssistantMessage, UserMessage
    from roboagent.agent.session import Session


@dataclass(slots=True)
class Run:
    session: "Session"
    config: RunConfig
    run_id: str = field(default_factory=lambda: uuid4().hex)
    _cancellation: RuntimeCancellation = field(
        default_factory=RuntimeCancellation, init=False, repr=False
    )
    _events: RunEventEmitter = field(init=False, repr=False)
    _execution_tree: ExecutionTree = field(init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _result: asyncio.Future[RunResult] | None = field(
        default=None, init=False, repr=False
    )
    _state: RunState = field(
        default_factory=lambda: RunState(RunPhase.IDLE, 0), init=False, repr=False
    )
    _skill_catalog: object | None = field(default=None, init=False, repr=False)
    _initial_message: "UserMessage | None" = field(default=None, init=False, repr=False)
    _initial_pending_sequence: int = field(default=0, init=False, repr=False)
    _nested_scope: ExecutionScope | None = field(default=None, init=False, repr=False)
    _output_processor: Callable[[object], Awaitable[object]] | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._execution_tree = ExecutionTree(
            root_run_id=self.run_id,
            cancellation=self._cancellation,
            deadline=absolute_deadline(self.config.timeout),
            budget=self.config.execution_budget,
            settlement_timeout=self.config.settlement_timeout,
            cleanup_timeout=self.config.cleanup_timeout,
            max_execution_records=self.config.max_execution_records,
            max_record_evidence_bytes=self.config.max_record_evidence_bytes,
        )
        self._events = RunEventEmitter(
            self.run_id,
            execution_tree=self._execution_tree,
            lineage=self._execution_tree.root_scope.lineage,
        )

    @property
    def state(self) -> RunState:
        return self._state

    def start_eager(self) -> None:
        if self._task is not None:
            raise RuntimeError("Run already started.")
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        self._task = loop.create_task(self._execute())

    def _attach_nested(
        self,
        *,
        tree: ExecutionTree,
        scope: ExecutionScope,
        events: RunEventEmitter,
        cancellation: RuntimeCancellation,
        output_processor: Callable[[object], Awaitable[object]],
    ) -> None:
        if self._task is not None:
            raise RuntimeError("Cannot attach a started Run.")
        self._execution_tree = tree
        self._nested_scope = scope
        self._events = events
        self._cancellation = cancellation
        self._output_processor = output_processor

    def cancel(self) -> None:
        self._cancellation.cancel(CancellationReason.USER)

    async def result(self) -> RunResult:
        if self._result is None:
            raise RuntimeError("Run has not started.")
        return await asyncio.shield(self._result)

    def subscribe(
        self, config: EventSubscriptionConfig | None = None
    ) -> EventSubscription:
        return self._events.subscribe(config)

    def _set_state(
        self,
        phase: RunPhase,
        turn: int,
        *,
        pending_tool_calls: tuple[ToolCallSummary, ...] = (),
    ) -> None:
        self._state = RunState(phase, turn, pending_tool_calls=pending_tool_calls)

    async def _execute(self) -> None:
        execution_scope = self._nested_scope or self._execution_tree.root_scope
        context = RunContext(
            self.run_id,
            self.session.session_id,
            self._cancellation,
            RuntimeRunExecutionContext(execution_scope),
        )
        status = RunStatus.FAILED
        output = None
        usage = None
        error: RunError | None = None
        cleanup_errors: list[RunError | CleanupError] = []
        effects: tuple[ToolEffectRecord, ...] = ()
        turns = 0
        timeout_task = (
            asyncio.create_task(self._timeout()) if self.config.timeout else None
        )
        manager = self.session.agent.skill_manager
        try:
            await self._events.emit(
                "child_run.started"
                if self._nested_scope is not None
                else "run.started",
                lineage=execution_scope.lineage,
            )
            await self._invoke_hooks("on_run_start", RunHookContext(context))
            await self.session._commit_initial_input(
                self.run_id,
                self._initial_message,
                self._initial_pending_sequence,
                self._cancellation,
            )
            tool_executor = ToolExecutor(
                registry=self.session.agent.tool_registry,
                policy=self.session.agent.tool_policy,
                hooks=self.session.agent.hooks,
                events=self._events,
                config=self.config.tool_executor,
                hook_timeout=self.config.hook_timeout,
                result_materializer=self.session.result_materializer,
                approval_provider=self.session.agent.approval_provider,
                approval_settings=self.session.agent.approval_settings,
                child_runner=self._run_child_agent,
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
            output, usage, effects, turns = (
                outcome.output,
                outcome.usage,
                outcome.effects,
                outcome.turns,
            )
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
            error = RunError(
                getattr(exc, "code", "runtime_error"),
                "Run execution failed.",
                cause_type=type(exc).__name__,
            )
            turns = self._state.turn
        finally:
            if timeout_task is not None:
                timeout_task.cancel()
                await asyncio.gather(timeout_task, return_exceptions=True)

        if (
            status is RunStatus.COMPLETED
            and output is not None
            and self._output_processor is not None
        ):
            try:
                output = cast("AssistantMessage", await self._output_processor(output))
            except asyncio.CancelledError:
                status = RunStatus.CANCELLED
                output = None
            except Exception as exc:
                status = RunStatus.FAILED
                output = None
                error = RunError(
                    getattr(exc, "code", "child_output_materialization_failed"),
                    "Child output materialization failed.",
                    cause_type=type(exc).__name__,
                )

        if self._nested_scope is None:
            await self._execution_tree.close()
            usage, usage_known = self._execution_tree.usage_result
            effects = self._execution_tree.effects
            tree_cleanup = self._execution_tree.cleanup_errors
            records = self._execution_tree.execution_records
            blockers = self._execution_tree.retry_blockers
        else:
            await self._execution_tree.close_scope(self._nested_scope)
            usage, usage_known = self._execution_tree.usage_for_scope(
                self._nested_scope
            )
            effects = self._execution_tree.effects_for_scope(self._nested_scope)
            tree_cleanup = self._execution_tree.cleanup_errors_for_scope(
                self._nested_scope
            )
            records = self._execution_tree.records_for_scope(self._nested_scope)
            blockers = self._execution_tree.blockers_for_scope(self._nested_scope)
        cleanup_errors.extend(tree_cleanup)
        cleanup_changes_status = any(
            self._execution_tree.scopes[item.scope_id].lineage.agent_depth
            == execution_scope.lineage.agent_depth
            for item in tree_cleanup
        )
        if cleanup_changes_status and status is RunStatus.COMPLETED and error is None:
            status = RunStatus.FAILED
            error = RunError("cleanup_error", "Execution resource cleanup failed.")

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
                    hook_result = await asyncio.wait_for(
                        value, self.config.cleanup_hook_timeout
                    )
                if hook_result is not None:
                    raise TypeError("on_run_end hook must return None.")
            except BaseException as exc:
                cleanup = RunError(
                    "cleanup_hook_error",
                    "on_run_end hook failed.",
                    cause_type=type(exc).__name__,
                )
                if provisional is RunStatus.COMPLETED and error is None:
                    status = RunStatus.FAILED
                    error = cleanup
                else:
                    cleanup_errors.append(cleanup)
        if manager is not None:
            try:
                manager.release_run(self.run_id)
            except Exception as exc:
                release_error = RunError(
                    "cleanup_error",
                    "Skill catalog cleanup failed.",
                    cause_type=type(exc).__name__,
                )
                if status is RunStatus.COMPLETED and error is None:
                    status = RunStatus.FAILED
                    error = release_error
                else:
                    cleanup_errors.append(release_error)
        try:
            await self.session.release_run(self.run_id)
        except Exception as exc:
            release_error = RunError(
                "cleanup_error",
                "Session ownership cleanup failed.",
                cause_type=type(exc).__name__,
            )
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
            retry_safe(effects, blockers),
            usage_known,
            records,
            self._execution_tree.execution_records_complete,
            blockers,
        )
        terminal = {
            RunStatus.COMPLETED: "run.completed",
            RunStatus.FAILED: "run.failed",
            RunStatus.CANCELLED: "run.cancelled",
        }[status]
        if self._nested_scope is not None:
            terminal = terminal.replace("run.", "child_run.")
        await self._events.emit(
            terminal,
            lineage=execution_scope.lineage,
            status=status.value,
            error_code=error.code if error else None,
        )
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
                raise _RunFailure(
                    RunError(
                        "hook_error",
                        f"{name} hook failed.",
                        cause_type=type(exc).__name__,
                    )
                ) from exc
        return tuple(results)

    async def _await_hook(self, value: object) -> object:
        task = asyncio.ensure_future(cast(Awaitable[object], value))
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

    async def _run_child_agent(
        self,
        parent_scope: ExecutionScope,
        agent: object,
        task: str,
        *,
        session_factory: object | None = None,
        run_config: RunConfig | None = None,
    ) -> RunResult:
        from roboagent.agent.agent import Agent
        from roboagent.agent.delegation import (
            ChildLifecycleError,
            ChildSessionContext,
            ChildSessionFactory,
            promote_child_output,
        )
        from roboagent.agent.session import Session
        from roboagent.message import AssistantMessage, UserMessage

        if not isinstance(agent, Agent):
            raise TypeError("run_child_agent requires a canonical Agent.")
        effective = run_config or agent.default_run_config
        child_cancellation = RuntimeCancellation(parent_scope.cancellation)
        child_id = uuid4().hex
        child_deadline = absolute_deadline(effective.timeout)
        scope = self._execution_tree.new_child_run_scope(
            parent=parent_scope,
            execution_run_id=child_id,
            cancellation=child_cancellation,
            deadline=child_deadline,
            agent_tool_name=parent_scope._tool_name or "agent",
        )
        parent_context = ChildSessionContext(
            self.session._root_session_id,
            self.session.workspace,
            self.session.result_materializer,
            self.session.artifact_reader,
            self.session.artifact_destination,
            self.session.repository,
            self.session.metadata,
        )
        child_session: object | None = None
        failure: BaseException | None = None
        result: RunResult | None = None
        try:
            if session_factory is None:
                child_session = Session(
                    agent,
                    session_id=uuid4().hex,
                    workspace=self.session.workspace,
                    result_materializer=self.session.result_materializer,
                    artifact_reader=self.session.artifact_reader,
                    artifact_destination=self.session.artifact_destination,
                )
                child_session._root_session_id = self.session._root_session_id
            else:
                try:
                    factory = cast(ChildSessionFactory, session_factory)
                    child_session = await factory.create(
                        parent=parent_context, agent=agent
                    )
                except Exception as exc:
                    raise ChildLifecycleError(
                        "child_session_creation_failed", "Child Session factory failed."
                    ) from exc
            if not isinstance(child_session, Session):
                raise ChildLifecycleError(
                    "invalid_child_session", "Factory returned an invalid Session."
                )
            if (
                child_session.agent != agent
                or child_session.closed
                or child_session.active_run_id is not None
                or child_session.messages
                or child_session.current_compaction is not None
                or await child_session.pending_inputs()
                or not isinstance(
                    getattr(child_session.workspace, "durable", None), bool
                )
                or not all(
                    callable(getattr(child_session.workspace, method, None))
                    for method in ("read", "write", "stat", "list", "delete")
                )
                or not callable(
                    getattr(child_session.result_materializer, "materialize", None)
                )
                or getattr(
                    child_session.result_materializer,
                    "workspace",
                    child_session.workspace,
                )
                is not child_session.workspace
                or not callable(
                    getattr(child_session.artifact_reader, "iter_bytes", None)
                )
                or not callable(
                    getattr(child_session.artifact_destination, "create_temp", None)
                )
            ):
                raise ChildLifecycleError(
                    "invalid_child_session", "Child Session is not isolated."
                )
            child_session._root_session_id = self.session._root_session_id

            async def promote(value: object) -> object:
                if not isinstance(value, AssistantMessage):
                    raise ChildLifecycleError(
                        "child_output_missing", "Child output is missing."
                    )
                return await promote_child_output(
                    value,
                    reader=child_session.artifact_reader,
                    destination=self.session.artifact_destination,
                    cancellation=child_cancellation,
                    max_bytes=effective.max_child_artifact_bytes,
                )

            child_run = child_session._start_nested(
                UserMessage(task),
                config=effective,
                tree=self._execution_tree,
                scope=scope,
                events=self._events,
                cancellation=child_cancellation,
                output_processor=promote,
            )
            try:
                result = await child_run.result()
            except asyncio.CancelledError:
                child_cancellation.cancel()
                await child_run.result()
                raise
        except (
            ExecutionRequestError,
            ChildLifecycleError,
            asyncio.CancelledError,
        ) as exc:
            failure = exc
        except Exception as exc:
            failure = ChildLifecycleError(
                "child_run_start_failed", "Child Run could not start."
            )
            failure.__cause__ = exc

        close_error: BaseException | None = None
        if isinstance(child_session, Session) and not child_session.closed:
            try:
                await child_session.close()
            except BaseException as exc:
                close_error = exc
        if failure is not None:
            raise failure
        if close_error is not None:
            raise ChildLifecycleError(
                "child_cleanup_failed", "Child Session close failed."
            ) from close_error
        if result is None:
            raise ChildLifecycleError(
                "child_run_start_failed", "Child Run produced no result."
            )
        return result
