"""Generic nested-execution scope, aggregation, and lifecycle primitives."""

from __future__ import annotations

import asyncio
import math
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from enum import Enum
from time import monotonic
from typing import TYPE_CHECKING, AsyncContextManager, Mapping, Protocol
from uuid import uuid4

from roboagent.message import (
    FrozenJsonObject,
    JsonValue,
    canonical_json_digest,
    canonical_json_dumps,
    freeze_json_object,
)
from roboagent.model import Usage
from roboagent.runtime._execution.budget import (
    ExecutionBudgetConfig,
    ExecutionBudgetView,
)
from roboagent.runtime._execution.child import (
    ChildRunExecutor as ChildRunExecutor,
    ChildRunRequest,
    ChildRunResult,
)
from roboagent.runtime._execution.facts import (
    CleanupError,
    ContributionId,
    EffectIdentity,
    ExecutionContribution,
    ExecutionRecord,
    ExecutionRecordStatus,
    ExecutionRecordType,
    RetryBlocker,
    RetryBlockerCode,
    SupplementalExecutionRecord,
    UsageContribution,
    UsageKnowledge,
)
from roboagent.runtime.types import CancellationToken

if TYPE_CHECKING:
    from roboagent.agent import Agent, RunConfig, RunResult
    from roboagent.message import ToolCall
    from roboagent.tool import ToolEffectRecord, ToolExecutionResult


class ExecutionInvariantError(RuntimeError):
    """The runtime received two incompatible facts for one execution identity."""


class ExecutionRequestError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ExecutionScopeState(Enum):
    OPEN = "open"
    CLOSING = "closing"
    FROZEN = "frozen"


@dataclass(frozen=True, slots=True)
class ExecutionLineage:
    root_run_id: str
    execution_run_id: str
    scope_id: str
    parent_scope_id: str | None
    scope_depth: int
    agent_depth: int
    tool_call_id: str | None = None
    agent_tool_name: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.root_run_id, self.execution_run_id, self.scope_id)
        ):
            raise ValueError("ExecutionLineage requires non-empty identities.")
        if self.parent_scope_id is not None and (
            not isinstance(self.parent_scope_id, str) or not self.parent_scope_id
        ):
            raise ValueError("parent_scope_id must be non-empty or None.")
        if any(
            type(value) is not int or value < 0
            for value in (self.scope_depth, self.agent_depth)
        ):
            raise ValueError("Execution lineage depths must be non-negative integers.")


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    usage: Usage | None
    usage_known: bool | None
    effects: tuple[ToolEffectRecord, ...]
    cleanup_errors: tuple[CleanupError, ...]
    records: tuple[ExecutionRecord, ...]
    records_complete: bool
    retry_blockers: tuple[RetryBlocker, ...]
    cleanup_affects_status: bool


class SettlementHandler(Protocol):
    async def settle(self) -> None: ...
    async def force_settle(self) -> None: ...


class ExecutionResource(Protocol):
    async def close(self) -> None: ...
    async def force_close(self) -> None: ...


class RunExecutionContext(Protocol):
    @property
    def lineage(self) -> ExecutionLineage: ...
    @property
    def cancellation(self): ...
    @property
    def deadline(self) -> float | None: ...
    @property
    def budget(self) -> ExecutionBudgetView: ...
    def tool_context(self, executor: object, session_id: str) -> ToolExecutionContext: ...
    def contribute_usage(self, usage: UsageContribution) -> None: ...
    def mark_tool_calls_committed(self, call_ids: tuple[str, ...]) -> None: ...


class ToolExecutionContext(RunExecutionContext, Protocol):
    async def execute_nested_tool(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> ToolExecutionResult: ...
    async def run_child_agent(
        self,
        agent: Agent,
        task: str,
        *,
        session_factory: object | None = None,
        run_config: RunConfig | None = None,
    ) -> RunResult: ...
    def settlement_barrier(
        self, *, handler: SettlementHandler, timeout: float | None = None
    ) -> AsyncContextManager[None]: ...
    def register_resource(self, resource: ExecutionResource) -> None: ...
    def add_retry_blocker(self, code: RetryBlockerCode, message: str) -> None: ...


class _SequenceAllocator:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        value = self.value
        self.value += 1
        return value


class ExecutionTree:
    """One root-wide source of nested execution facts."""

    def __init__(
        self,
        *,
        root_run_id: str,
        cancellation: CancellationToken,
        deadline: float | None,
        budget: ExecutionBudgetConfig,
        settlement_timeout: float,
        cleanup_timeout: float,
        max_execution_records: int,
        max_record_evidence_bytes: int,
    ) -> None:
        self.root_run_id = root_run_id
        self.cancellation = cancellation
        self.deadline = deadline
        self.budget_config = budget
        self.settlement_timeout = settlement_timeout
        self.cleanup_timeout = cleanup_timeout
        self.max_execution_records = max_execution_records
        self.max_record_evidence_bytes = max_record_evidence_bytes
        self.scope_sequences = _SequenceAllocator()
        self.event_sequences = _SequenceAllocator()
        self.record_sequences = _SequenceAllocator()
        self.child_runs_used = 0
        self.nested_tools_used = 0
        self._contributions: dict[ContributionId, ExecutionContribution] = {}
        self._usage_state: UsageKnowledge | None = None
        self._usage: Usage | None = None
        self._effects: dict[EffectIdentity, ToolEffectRecord] = {}
        self._records: list[ExecutionRecord] = []
        self._records_complete = True
        self._cleanup_errors: list[CleanupError] = []
        self._retry_blockers: list[RetryBlocker] = []
        self.scopes: dict[str, ExecutionScope] = {}
        self.root_scope = self._new_scope(
            execution_run_id=root_run_id,
            parent=None,
            agent_depth=0,
            cancellation=cancellation,
            deadline=deadline,
        )

    def _new_scope(
        self,
        *,
        execution_run_id: str,
        parent: ExecutionScope | None,
        agent_depth: int,
        cancellation: CancellationToken,
        deadline: float | None,
        tool_call_id: str | None = None,
        agent_tool_name: str | None = None,
    ) -> ExecutionScope:
        scope_id = uuid4().hex
        lineage = ExecutionLineage(
            self.root_run_id,
            execution_run_id,
            scope_id,
            parent.lineage.scope_id if parent else None,
            parent.lineage.scope_depth + 1 if parent else 0,
            agent_depth,
            tool_call_id,
            agent_tool_name,
        )
        scope = ExecutionScope(
            self, lineage, self.scope_sequences.next(), cancellation, deadline
        )
        self.scopes[scope_id] = scope
        if parent is not None:
            parent._children.append(scope)
        return scope

    @property
    def budget_view(self) -> ExecutionBudgetView:
        return ExecutionBudgetView(
            self.budget_config.max_agent_depth,
            max(0, self.budget_config.max_child_runs - self.child_runs_used),
            max(0, self.budget_config.max_nested_tool_calls - self.nested_tools_used),
        )

    def accept_nested_tool(self) -> bool:
        if self.nested_tools_used >= self.budget_config.max_nested_tool_calls:
            return False
        self.nested_tools_used += 1
        return True

    def new_child_run_scope(
        self,
        *,
        parent: ExecutionScope,
        execution_run_id: str,
        cancellation: CancellationToken,
        deadline: float | None,
        agent_tool_name: str,
    ) -> ExecutionScope:
        parent._require_open("create child Run")
        agent_depth = parent.lineage.agent_depth + 1
        if agent_depth > self.budget_config.max_agent_depth:
            raise ExecutionRequestError(
                "agent_depth_exceeded", "Agent delegation depth exceeded."
            )
        if self.child_runs_used >= self.budget_config.max_child_runs:
            raise ExecutionRequestError(
                "child_run_budget_exceeded", "Child Run budget exceeded."
            )
        self.child_runs_used += 1
        return self._new_scope(
            execution_run_id=execution_run_id,
            parent=parent,
            agent_depth=agent_depth,
            cancellation=cancellation,
            deadline=_minimum_deadline(parent.deadline, deadline),
            agent_tool_name=agent_tool_name,
        )

    def contribute(
        self, contribution: ExecutionContribution, *, settlement: bool = False
    ) -> None:
        scope = self.scopes.get(contribution.contribution_id.scope_id)
        if scope is None:
            raise ExecutionInvariantError("Contribution references an unknown scope.")
        if scope.state is ExecutionScopeState.FROZEN or (
            scope.state is ExecutionScopeState.CLOSING and not settlement
        ):
            raise ExecutionInvariantError(
                "ExecutionScope no longer accepts this contribution."
            )
        previous = self._contributions.get(contribution.contribution_id)
        if previous is not None:
            if previous == contribution:
                return
            raise ExecutionInvariantError(
                "Contribution identity was reused with different content."
            )
        self._contributions[contribution.contribution_id] = contribution
        if contribution.usage is not None:
            self._merge_usage(contribution.usage)
        for effect in contribution.effects:
            effect_id = getattr(effect, "effect_id", None)
            if not isinstance(effect_id, EffectIdentity):
                raise ExecutionInvariantError("Runtime effects require EffectIdentity.")
            previous_effect = self._effects.get(effect_id)
            if previous_effect is not None and previous_effect != effect:
                raise ExecutionInvariantError(
                    "Effect identity was reused with different content."
                )
            self._effects[effect_id] = effect
        for record in contribution.records:
            self.add_summary_record(scope, record)
        self._cleanup_errors.extend(contribution.cleanup_errors)

    def _merge_usage(self, contribution: UsageContribution) -> None:
        if self._usage_state is UsageKnowledge.UNKNOWN:
            return
        if contribution.state is UsageKnowledge.UNKNOWN:
            self._usage_state = UsageKnowledge.UNKNOWN
            self._usage = None
            return
        assert contribution.usage is not None
        if self._usage_state is None:
            self._usage_state = UsageKnowledge.KNOWN
            self._usage = contribution.usage
        else:
            assert self._usage is not None
            self._usage = _merge_known_usage(self._usage, contribution.usage)

    def add_tool_record(
        self,
        scope: ExecutionScope,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: FrozenJsonObject,
        arguments_preview: FrozenJsonObject | None,
        status: ExecutionRecordStatus,
        error_code: str | None,
        evidence: FrozenJsonObject | None,
    ) -> None:
        preview = self._bounded_preview(arguments_preview)
        bounded_evidence = self._bounded_evidence(evidence)
        self._append_record(
            ExecutionRecord(
                self.record_sequences.next(),
                ExecutionRecordType.TOOL,
                self.root_run_id,
                scope.lineage.execution_run_id,
                scope.lineage.scope_id,
                tool_call_id,
                tool_name,
                _safe_json_digest(arguments),
                preview,
                status,
                error_code,
                bounded_evidence,
            )
        )

    def add_summary_record(
        self, scope: ExecutionScope, record: SupplementalExecutionRecord
    ) -> None:
        self._append_record(
            ExecutionRecord(
                self.record_sequences.next(),
                ExecutionRecordType.SUMMARY,
                self.root_run_id,
                scope.lineage.execution_run_id,
                scope.lineage.scope_id,
                None,
                None,
                None,
                None,
                record.status,
                record.error_code,
                self._bounded_evidence(record.evidence),
            )
        )

    def _append_record(self, record: ExecutionRecord) -> None:
        if not self._records_complete:
            return
        if len(self._records) < self.max_execution_records - 1:
            self._records.append(record)
            return
        self._records_complete = False
        overflow = ExecutionRecord(
            self.record_sequences.next(),
            ExecutionRecordType.SUMMARY,
            self.root_run_id,
            record.execution_run_id,
            record.scope_id,
            None,
            None,
            None,
            None,
            ExecutionRecordStatus.UNKNOWN,
            "execution_record_overflow",
            None,
        )
        if len(self._records) == self.max_execution_records:
            self._records[-1] = overflow
        else:
            self._records.append(overflow)

    def _bounded_preview(
        self, value: FrozenJsonObject | None
    ) -> FrozenJsonObject | None:
        if value is None:
            return None
        try:
            return (
                value if _json_size(value) <= self.max_record_evidence_bytes else None
            )
        except (TypeError, ValueError, UnicodeError):
            return None

    def _bounded_evidence(
        self, value: FrozenJsonObject | None
    ) -> FrozenJsonObject | None:
        if value is None:
            return None
        try:
            size = _json_size(value)
        except (TypeError, ValueError, UnicodeError):
            return None
        if size <= self.max_record_evidence_bytes:
            return value
        envelope = freeze_json_object(
            {"omitted": True, "digest": canonical_json_digest(value), "size": size}
        )
        return (
            envelope if _json_size(envelope) <= self.max_record_evidence_bytes else None
        )

    def add_retry_blocker(self, blocker: RetryBlocker) -> None:
        if blocker not in self._retry_blockers:
            self._retry_blockers.append(blocker)

    def mark_effects_committed(self, effect_ids: tuple[EffectIdentity, ...]) -> None:
        for effect_id in effect_ids:
            effect = self._effects.get(effect_id)
            if effect is not None:
                self._effects[effect_id] = replace(effect, transcript_committed=True)

    def mark_tool_calls_committed(self, call_ids: tuple[str, ...]) -> None:
        accepted = set(call_ids)
        root_id = self.root_scope.lineage.scope_id
        for effect_id, effect in tuple(self._effects.items()):
            scope = self.scopes[effect_id.scope_id]
            while scope.lineage.parent_scope_id not in (None, root_id):
                parent = self.scopes.get(scope.lineage.parent_scope_id)
                if parent is None:
                    break
                scope = parent
            if scope.lineage.tool_call_id in accepted:
                self._effects[effect_id] = replace(effect, transcript_committed=True)

    @property
    def usage_result(self) -> tuple[Usage | None, bool | None]:
        if self._usage_state is None:
            return None, None
        if self._usage_state is UsageKnowledge.UNKNOWN:
            return None, False
        return self._usage, True

    @property
    def effects(self) -> tuple[ToolEffectRecord, ...]:
        def key(effect: ToolEffectRecord) -> tuple[int, int]:
            effect_id = effect.effect_id
            if effect_id is None:
                raise ExecutionInvariantError(
                    "Runtime effect is missing EffectIdentity."
                )
            return self.scopes[effect_id.scope_id].scope_sequence, effect_id.sequence

        return tuple(sorted(self._effects.values(), key=key))

    @property
    def execution_records(self) -> tuple[ExecutionRecord, ...]:
        return tuple(self._records)

    @property
    def execution_records_complete(self) -> bool:
        return self._records_complete

    @property
    def cleanup_errors(self) -> tuple[CleanupError, ...]:
        return tuple(self._cleanup_errors)

    @property
    def retry_blockers(self) -> tuple[RetryBlocker, ...]:
        return tuple(self._retry_blockers)

    def usage_for_scope(
        self, scope: ExecutionScope
    ) -> tuple[Usage | None, bool | None]:
        state: UsageKnowledge | None = None
        value: Usage | None = None
        for contribution in self._contributions.values():
            if contribution.usage is None or not self._is_descendant(
                contribution.contribution_id.scope_id, scope.lineage.scope_id
            ):
                continue
            if state is UsageKnowledge.UNKNOWN:
                continue
            if contribution.usage.state is UsageKnowledge.UNKNOWN:
                state, value = UsageKnowledge.UNKNOWN, None
            elif state is None:
                state, value = UsageKnowledge.KNOWN, contribution.usage.usage
            else:
                assert value is not None and contribution.usage.usage is not None
                value = _merge_known_usage(value, contribution.usage.usage)
        if state is None:
            return None, None
        return (None, False) if state is UsageKnowledge.UNKNOWN else (value, True)

    def effects_for_scope(self, scope: ExecutionScope) -> tuple[ToolEffectRecord, ...]:
        return tuple(
            effect
            for effect in self.effects
            if effect.effect_id is not None
            and self._is_descendant(effect.effect_id.scope_id, scope.lineage.scope_id)
        )

    def records_for_scope(self, scope: ExecutionScope) -> tuple[ExecutionRecord, ...]:
        return tuple(
            record
            for record in self._records
            if self._is_descendant(record.scope_id, scope.lineage.scope_id)
        )

    def blockers_for_scope(self, scope: ExecutionScope) -> tuple[RetryBlocker, ...]:
        return tuple(
            blocker
            for blocker in self._retry_blockers
            if self._is_descendant(blocker.scope_id, scope.lineage.scope_id)
        )

    def cleanup_errors_for_scope(
        self, scope: ExecutionScope
    ) -> tuple[CleanupError, ...]:
        return tuple(
            error
            for error in self._cleanup_errors
            if self._is_descendant(error.scope_id, scope.lineage.scope_id)
        )

    def _is_descendant(self, scope_id: str, ancestor_id: str) -> bool:
        current = self.scopes.get(scope_id)
        while current is not None:
            if current.lineage.scope_id == ancestor_id:
                return True
            parent_id = current.lineage.parent_scope_id
            current = self.scopes.get(parent_id) if parent_id is not None else None
        return False

    async def close(self) -> None:
        await self.close_scope(self.root_scope)

    async def close_scope(self, root: ExecutionScope) -> None:
        root.begin_closing()
        descendants = tuple(
            scope
            for scope in self.scopes.values()
            if self._is_descendant(scope.lineage.scope_id, root.lineage.scope_id)
        )
        for scope in sorted(
            descendants, key=lambda item: item.scope_sequence, reverse=True
        ):
            if scope.state is ExecutionScopeState.OPEN:
                scope.begin_closing()
        for scope in sorted(
            descendants, key=lambda item: item.scope_sequence, reverse=True
        ):
            await scope._wait_for_settlement()
        for scope in sorted(
            descendants, key=lambda item: item.scope_sequence, reverse=True
        ):
            await scope._cleanup()
            scope.freeze()


class ExecutionScope:
    def __init__(
        self,
        tree: ExecutionTree,
        lineage: ExecutionLineage,
        scope_sequence: int,
        cancellation: CancellationToken,
        deadline: float | None,
    ) -> None:
        self._tree = tree
        self.lineage = lineage
        self.scope_sequence = scope_sequence
        self.cancellation = cancellation
        self.deadline = deadline
        self.state = ExecutionScopeState.OPEN
        self._effect_sequence = _SequenceAllocator()
        self._contribution_sequence = _SequenceAllocator()
        self._resources: list[ExecutionResource] = []
        self._children: list[ExecutionScope] = []
        self._terminal_recorded = False
        self._tool_name: str | None = None
        self._active_settlements = 0
        self._settlement_idle = asyncio.Event()
        self._settlement_idle.set()

    @property
    def budget(self) -> ExecutionBudgetView:
        return self._tree.budget_view

    def child_tool(
        self,
        *,
        tool_call_id: str,
        tool_name: str | None = None,
        deadline: float | None = None,
    ) -> ExecutionScope:
        from roboagent.runtime.types import RuntimeCancellation

        self._require_open("create child scope")
        cancellation = RuntimeCancellation(self.cancellation)
        scope = self._tree._new_scope(
            execution_run_id=self.lineage.execution_run_id,
            parent=self,
            agent_depth=self.lineage.agent_depth,
            cancellation=cancellation,
            deadline=_minimum_deadline(self.deadline, deadline),
            tool_call_id=tool_call_id,
        )
        scope._tool_name = tool_name
        return scope

    def next_effect_id(self) -> EffectIdentity:
        return EffectIdentity(self.lineage.scope_id, self._effect_sequence.next())

    def next_contribution_id(self) -> ContributionId:
        return ContributionId(self.lineage.scope_id, self._contribution_sequence.next())

    def contribute(
        self, contribution: ExecutionContribution, *, settlement: bool = False
    ) -> None:
        self._tree.contribute(contribution, settlement=settlement)

    def register_resource(self, resource: ExecutionResource) -> None:
        self._require_open("register resource")
        if not callable(getattr(resource, "close", None)) or not callable(
            getattr(resource, "force_close", None)
        ):
            raise TypeError(
                "ExecutionResource must implement close() and force_close()."
            )
        self._resources.append(resource)

    @property
    def settlement_active(self) -> bool:
        return self._active_settlements > 0

    def _enter_settlement(self) -> None:
        self._require_open("enter settlement barrier")
        self._active_settlements += 1
        self._settlement_idle.clear()

    def _leave_settlement(self) -> None:
        if self._active_settlements <= 0:
            raise ExecutionInvariantError("Settlement barrier lifecycle underflow.")
        self._active_settlements -= 1
        if self._active_settlements == 0:
            self._settlement_idle.set()

    async def _wait_for_settlement(self) -> None:
        await self._settlement_idle.wait()

    def begin_closing(self) -> None:
        if self.state is ExecutionScopeState.OPEN:
            self.state = ExecutionScopeState.CLOSING

    def freeze(self) -> None:
        if self.state is ExecutionScopeState.OPEN:
            raise ExecutionInvariantError("OPEN scope cannot be frozen directly.")
        self.state = ExecutionScopeState.FROZEN

    def _require_open(self, operation: str) -> None:
        if self.state is not ExecutionScopeState.OPEN:
            raise ExecutionInvariantError(f"Cannot {operation} outside an OPEN scope.")

    async def _cleanup(self) -> None:
        resources, self._resources = self._resources, []
        for resource in reversed(resources):
            forced = False
            force_failed = False
            error: BaseException | None = None
            try:
                await asyncio.wait_for(resource.close(), self._tree.cleanup_timeout)
                continue
            except BaseException as exc:
                error = exc
            try:
                forced = True
                await asyncio.wait_for(
                    resource.force_close(), self._tree.cleanup_timeout
                )
            except BaseException as exc:
                error = exc
                force_failed = True
            cleanup = CleanupError(
                self.lineage.scope_id,
                type(resource).__name__,
                "cleanup_timeout"
                if isinstance(error, TimeoutError)
                else "cleanup_failed",
                "Execution resource cleanup did not settle.",
                forced,
            )
            self._tree._cleanup_errors.append(cleanup)
            if force_failed:
                self._tree.add_retry_blocker(
                    RetryBlocker(
                        RetryBlockerCode.CLEANUP_UNCERTAIN,
                        self.lineage.scope_id,
                        cleanup.message,
                    )
                )


class RuntimeRunExecutionContext:
    def __init__(self, scope: ExecutionScope) -> None:
        self._scope = scope

    @classmethod
    def create_root(
        cls,
        *,
        root_run_id: str,
        cancellation: CancellationToken,
        deadline: float | None,
        budget: ExecutionBudgetConfig,
        settlement_timeout: float,
        cleanup_timeout: float,
        max_execution_records: int,
        max_record_evidence_bytes: int,
    ) -> RuntimeRunExecutionContext:
        tree = ExecutionTree(
            root_run_id=root_run_id,
            cancellation=cancellation,
            deadline=deadline,
            budget=budget,
            settlement_timeout=settlement_timeout,
            cleanup_timeout=cleanup_timeout,
            max_execution_records=max_execution_records,
            max_record_evidence_bytes=max_record_evidence_bytes,
        )
        return cls(tree.root_scope)

    @property
    def lineage(self) -> ExecutionLineage:
        return self._scope.lineage

    @property
    def cancellation(self):
        return self._scope.cancellation

    @property
    def deadline(self) -> float | None:
        return self._scope.deadline

    @property
    def budget(self) -> ExecutionBudgetView:
        return self._scope.budget

    def tool_context(
        self, executor: object, session_id: str
    ) -> RuntimeToolExecutionContext:
        return RuntimeToolExecutionContext(self._scope, executor, session_id)

    def contribute_usage(self, usage: UsageContribution) -> None:
        self._scope.contribute(
            ExecutionContribution(self._scope.next_contribution_id(), usage=usage)
        )

    def mark_tool_calls_committed(self, call_ids: tuple[str, ...]) -> None:
        if self.lineage.execution_run_id == self.lineage.root_run_id:
            self._scope._tree.mark_tool_calls_committed(call_ids)

    def next_event_sequence(self) -> int:
        return self._scope._tree.event_sequences.next()

    async def finalize(self) -> ExecutionSummary:
        tree = self._scope._tree
        is_root = self._scope is tree.root_scope
        if is_root:
            await tree.close()
            usage, usage_known = tree.usage_result
            effects = tree.effects
            cleanup_errors = tree.cleanup_errors
            records = tree.execution_records
            retry_blockers = tree.retry_blockers
        else:
            await tree.close_scope(self._scope)
            usage, usage_known = tree.usage_for_scope(self._scope)
            effects = tree.effects_for_scope(self._scope)
            cleanup_errors = tree.cleanup_errors_for_scope(self._scope)
            records = tree.records_for_scope(self._scope)
            retry_blockers = tree.blockers_for_scope(self._scope)
        cleanup_affects_status = any(
            tree.scopes[item.scope_id].lineage.agent_depth
            == self.lineage.agent_depth
            for item in cleanup_errors
        )
        return ExecutionSummary(
            usage,
            usage_known,
            effects,
            cleanup_errors,
            records,
            tree.execution_records_complete,
            retry_blockers,
            cleanup_affects_status,
        )


class RuntimeToolExecutionContext(RuntimeRunExecutionContext):
    def __init__(
        self, scope: ExecutionScope, executor: object, session_id: str = "nested"
    ) -> None:
        super().__init__(scope)
        self._executor = executor
        self._session_id = session_id

    async def execute_nested_tool(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> ToolExecutionResult:
        from roboagent.message import ToolCall
        from roboagent.tool import ToolBatchAborted, ToolErrorInfo, ToolExecutionResult

        self._scope._require_open("execute nested tool")
        if not isinstance(name, str) or not name or not isinstance(arguments, Mapping):
            safe_name = name if isinstance(name, str) and name else "<invalid>"
            return ToolExecutionResult(
                uuid4().hex,
                safe_name,
                error=ToolErrorInfo(
                    "invalid_arguments", "Invalid nested Tool request."
                ),
            )
        call = ToolCall(uuid4().hex, name, freeze_json_object(arguments))
        validator = getattr(self._executor, "validate_nested", None)
        if callable(validator):
            invalid = validator(call)
            if invalid is not None:
                return invalid
        if not self._scope._tree.accept_nested_tool():
            return ToolExecutionResult(
                call.id,
                name,
                error=ToolErrorInfo(
                    "nested_tool_budget_exceeded", "Nested Tool budget exceeded."
                ),
            )
        context_factory = getattr(self._executor, "execute_nested", None)
        if not callable(context_factory):
            return ToolExecutionResult(
                call.id,
                name,
                error=ToolErrorInfo(
                    "nested_execution_unavailable", "Nested execution is unavailable."
                ),
            )
        try:
            return await context_factory(call, self, self._session_id)
        except ToolBatchAborted as exc:
            return ToolExecutionResult(
                call.id,
                name,
                error=ToolErrorInfo(
                    exc.reason.code, exc.reason.message, exc.reason.retryable
                ),
            )

    async def run_child_agent(
        self, agent, task: str, *, session_factory=None, run_config=None
    ):
        runner = getattr(self._executor, "run_child", None)
        if not callable(runner):
            raise ExecutionRequestError(
                "nested_execution_unavailable", "Child Agent execution is unavailable."
            )
        child = await runner(
            ChildRunRequest(agent, task, session_factory, run_config), self
        )
        if not isinstance(child, ChildRunResult):
            raise ExecutionInvariantError(
                "ChildRunExecutor returned a non-canonical result."
            )
        return child.result

    def child_tool_context(
        self, call: ToolCall, executor: object, session_id: str
    ) -> RuntimeToolExecutionContext:
        scope = self._scope.child_tool(tool_call_id=call.id, tool_name=call.name)
        return RuntimeToolExecutionContext(scope, executor, session_id)

    def begin_child_run(
        self,
        *,
        execution_run_id: str,
        cancellation: CancellationToken,
        deadline: float | None,
    ) -> RuntimeRunExecutionContext:
        scope = self._scope._tree.new_child_run_scope(
            parent=self._scope,
            execution_run_id=execution_run_id,
            cancellation=cancellation,
            deadline=deadline,
            agent_tool_name=self._scope._tool_name or "agent",
        )
        return RuntimeRunExecutionContext(scope)

    def cap_deadline(self, deadline: float) -> None:
        self._scope.deadline = _minimum_deadline(self._scope.deadline, deadline)

    @property
    def settlement_active(self) -> bool:
        return self._scope.settlement_active

    async def close_tool_scope(self) -> None:
        if self._scope._terminal_recorded:
            return
        self._scope.begin_closing()
        await self._scope._cleanup()
        self._scope.freeze()

    def next_effect_id(self) -> EffectIdentity:
        return self._scope.next_effect_id()

    def contribute_effects(self, effects: tuple[ToolEffectRecord, ...]) -> None:
        if effects:
            self._scope.contribute(
                ExecutionContribution(
                    self._scope.next_contribution_id(), effects=effects
                )
            )

    def contribute_composite(
        self,
        effects: tuple[ToolEffectRecord, ...],
        records: tuple[SupplementalExecutionRecord, ...],
    ) -> None:
        self._scope.contribute(
            ExecutionContribution(
                self._scope.next_contribution_id(), effects=effects, records=records
            )
        )

    def record_tool_call(
        self,
        *,
        call: ToolCall,
        arguments_preview: FrozenJsonObject | None,
        status: ExecutionRecordStatus,
        error_code: str | None,
        evidence: FrozenJsonObject | None,
    ) -> None:
        self._scope._tree.add_tool_record(
            self._scope,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
            arguments_preview=arguments_preview,
            status=status,
            error_code=error_code,
            evidence=evidence,
        )
        self._scope._terminal_recorded = True

    def settlement_barrier(
        self, *, handler: SettlementHandler, timeout: float | None = None
    ) -> AsyncContextManager[None]:
        self._scope._require_open("enter settlement barrier")
        return _SettlementBarrier(
            self._scope,
            handler,
            self._scope._tree.settlement_timeout
            if timeout is None
            else _positive_timeout(timeout),
        )

    def register_resource(self, resource: ExecutionResource) -> None:
        self._scope.register_resource(resource)

    def add_retry_blocker(self, code: RetryBlockerCode, message: str) -> None:
        self._scope._require_open("add retry blocker")
        self._scope._tree.add_retry_blocker(
            RetryBlocker(code, self.lineage.scope_id, message)
        )


class _SettlementBarrier(AbstractAsyncContextManager[None]):
    def __init__(
        self, scope: ExecutionScope, handler: SettlementHandler, timeout: float
    ) -> None:
        if not callable(getattr(handler, "settle", None)) or not callable(
            getattr(handler, "force_settle", None)
        ):
            raise TypeError(
                "SettlementHandler must implement settle() and force_settle()."
            )
        self.scope = scope
        self.handler = handler
        self.timeout = timeout
        self._entered = False

    async def __aenter__(self) -> None:
        if self._entered:
            raise RuntimeError("Settlement barrier cannot be entered twice.")
        self._entered = True
        self.scope._enter_settlement()
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        driver = asyncio.create_task(self._drive_settlement())
        cancellation_pending = False
        try:
            while True:
                try:
                    await asyncio.shield(driver)
                    break
                except asyncio.CancelledError as cancellation:
                    if driver.cancelled():
                        self._mark_uncertain()
                        raise SettlementError(
                            "settlement_failed",
                            "Settlement driver was interrupted.",
                        ) from cancellation
                    # Cancellation is observable by the caller only after the
                    # independently owned settlement driver has converged.
                    cancellation_pending = True
                    continue
            if cancellation_pending:
                raise asyncio.CancelledError()
        finally:
            self.scope._leave_settlement()
        return False

    async def _drive_settlement(self) -> None:
        try:
            await asyncio.wait_for(self.handler.settle(), self.timeout)
            return
        except TimeoutError:
            try:
                await asyncio.wait_for(self.handler.force_settle(), self.timeout)
                return
            except BaseException as force_error:
                self._mark_uncertain()
                raise SettlementError(
                    "settlement_timeout", "Settlement could not be confirmed."
                ) from force_error
        except BaseException as settle_error:
            try:
                await asyncio.wait_for(self.handler.force_settle(), self.timeout)
            except BaseException:
                self._mark_uncertain()
            raise SettlementError(
                "settlement_failed", "Settlement handler failed."
            ) from settle_error

    def _mark_uncertain(self) -> None:
        self.scope._tree.add_retry_blocker(
            RetryBlocker(
                RetryBlockerCode.SETTLEMENT_UNCERTAIN,
                self.scope.lineage.scope_id,
                "Settlement could not be confirmed.",
            )
        )


class SettlementError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _merge_known_usage(left: Usage, right: Usage) -> Usage:
    def merge(a: int | None, b: int | None) -> int | None:
        return a + b if a is not None and b is not None else None

    return Usage(
        merge(left.input_tokens, right.input_tokens),
        merge(left.output_tokens, right.output_tokens),
        merge(left.total_tokens, right.total_tokens),
    )


def _json_size(value: object) -> int:
    return len(canonical_json_dumps(value).encode("utf-8"))


def _safe_json_digest(value: object) -> str | None:
    try:
        return canonical_json_digest(value)
    except (TypeError, ValueError, UnicodeError):
        return None


def _minimum_deadline(left: float | None, right: float | None) -> float | None:
    values = tuple(value for value in (left, right) if value is not None)
    return min(values) if values else None


def _positive_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("Settlement timeout must be positive.")
    return float(value)


def absolute_deadline(timeout: float | None) -> float | None:
    return None if timeout is None else monotonic() + timeout
