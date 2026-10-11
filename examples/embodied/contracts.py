"""Host physical execution checks composed from canonical World and Tool data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import NoReturn

from roboagent.message import ToolCall, canonical_json_digest
from roboagent.model import ModelResponse
from roboagent.agent import (
    HookDecision,
    ModelHookContext,
    RunEndHookContext,
    RunHookContext,
    ToolHookContext,
)
from roboagent.runtime.execution import (
    ExecutionScopeState,
    RuntimeRunExecutionContext,
    RuntimeToolExecutionContext,
)
from roboagent.tool import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolDecision,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolExecutionMode,
    ToolPolicyDecision,
)
from roboagent.world import (
    EntityRef,
    World,
    WorldEntryKey,
    WorldSnapshot,
    WorldStatus,
    ClockResolver,
)


def reject(code: str) -> NoReturn:
    raise ToolExecutionFailure(ToolErrorInfo(code, code))


class _RunPolicyResource:
    """Host policy state cleaned by the canonical Run resource lifecycle."""

    def __init__(self, policy: PhysicalPolicy, execution: RuntimeRunExecutionContext):
        self.policy = policy
        self.execution = execution
        self.message_id: str | None = None

    async def close(self) -> None:
        run_id = self.execution.lineage.execution_run_id
        self.policy._runs.pop(run_id, None)
        self.policy._clear_turns(run_id)

    async def force_close(self) -> None:
        await self.close()


class _NestedCallResource:
    def __init__(self, policy: PhysicalPolicy, parent_id: str, scope_id: str):
        self.policy, self.parent_id, self.scope_id = policy, parent_id, scope_id

    async def close(self) -> None:
        if self.policy._nested_active.get(self.parent_id) == self.scope_id:
            self.policy._nested_active.pop(self.parent_id)

    async def force_close(self) -> None:
        await self.close()


class PhysicalPolicy:
    """Embodied-only rules composed from Policy, hooks and ExecutionResource.

    Register the same instance as Agent.tool_policy and an Agent hook. Physical
    Tools use this instance for validation; no action state belongs to Core.
    """

    def __init__(
        self, physical: frozenset[str], wrappers: frozenset[str] = frozenset()
    ):
        self.physical, self.wrappers = frozenset(physical), frozenset(wrappers)
        if self.physical & self.wrappers:
            raise ValueError(
                "Physical actions and wrappers must have distinct classifications."
            )
        self._runs: dict[str, _RunPolicyResource] = {}
        self._turn_actions: dict[tuple[str, str], str] = {}
        self._nested_active: dict[str, str] = {}

    def _clear_turns(self, run_id: str) -> None:
        for key in tuple(self._turn_actions):
            if key[0] == run_id:
                self._turn_actions.pop(key)

    def bind_run(self, execution: RuntimeRunExecutionContext) -> None:
        """Bind trusted Host execution; on_run_start does this for Agent Runs."""
        run_id = execution.lineage.execution_run_id
        if run_id in self._runs:
            raise ValueError("Physical policy already owns this Run.")
        state = _RunPolicyResource(self, execution)
        execution.tool_context(self, "physical-policy").register_resource(state)
        self._runs[run_id] = state

    def begin_turn(self, run_id: str, message_id: str) -> None:
        state = self._runs[run_id]
        self._clear_turns(run_id)
        state.message_id = message_id

    async def on_run_start(self, context: RunHookContext) -> None:
        execution = context.run_context.execution
        if not isinstance(execution, RuntimeRunExecutionContext):
            raise TypeError("Physical policy requires canonical execution.")
        self.bind_run(execution)

    async def before_model(self, context: ModelHookContext) -> HookDecision:
        self._runs[context.run_context.run_id].message_id = None
        return HookDecision.CONTINUE

    async def after_model(
        self, context: ModelHookContext, response: ModelResponse
    ) -> None:
        self.begin_turn(context.run_context.run_id, response.message.message_id)

    async def before_tool(
        self, context: ToolHookContext, call: ToolCall
    ) -> HookDecision:
        return HookDecision.CONTINUE

    async def after_tool(
        self, context: ToolHookContext, result: ToolExecutionResult
    ) -> None:
        pass

    async def on_run_end(self, context: RunEndHookContext) -> None:
        self._runs.pop(context.run_context.run_id, None)
        self._clear_turns(context.run_context.run_id)

    def _active(self, context: ToolContext) -> bool:
        state = self._runs.get(context.run_id)
        execution = context.execution
        return (
            state is not None
            and isinstance(execution, RuntimeToolExecutionContext)
            and state.execution.scope_state is ExecutionScopeState.OPEN
            and execution.scope_state is ExecutionScopeState.OPEN
            and not context.cancellation.cancelled
            and context.message_id is not None
            and state.message_id == context.message_id
        )

    def claim_turn_action(self, context: ToolContext) -> bool:
        if not self._active(context):
            return False
        assert context.message_id is not None and context.execution is not None
        key = (context.run_id, context.message_id)
        if key in self._turn_actions:
            return False
        self._turn_actions[key] = context.execution.lineage.scope_id
        return True

    def owns_turn_action(self, context: ToolContext) -> bool:
        if not self._active(context):
            return False
        assert context.message_id is not None and context.execution is not None
        return (
            self._turn_actions.get((context.run_id, context.message_id))
            == context.execution.lineage.scope_id
        )

    async def evaluate(
        self, call: ToolCall, tool: Tool | None, context: ToolContext
    ) -> ToolDecision | ToolPolicyDecision:
        execution = context.execution
        if call.name in self.physical | self.wrappers:
            if tool is None or tool.execution_mode is not ToolExecutionMode.SERIAL:
                return ToolPolicyDecision(
                    ToolDecision.REJECT, "physical_serial_required"
                )
            if not isinstance(execution, RuntimeToolExecutionContext):
                return ToolPolicyDecision(
                    ToolDecision.REJECT, "decision_context_missing"
                )
            if any(name not in self.wrappers for name in execution.ancestor_tool_names):
                return ToolPolicyDecision(
                    ToolDecision.REJECT, "physical_wrapper_not_authorized"
                )
        # SERIAL constrains batches. Host-authored wrappers must also await their
        # nested calls: policy admission rejects overlap without another scheduler.
        if isinstance(execution, RuntimeToolExecutionContext) and (
            execution.ancestor_tool_names
            and execution.ancestor_tool_names[-1] in self.wrappers
        ):
            parent_id = execution.lineage.parent_scope_id
            assert parent_id is not None
            if parent_id in self._nested_active:
                return ToolPolicyDecision(
                    ToolDecision.REJECT, "concurrent_nested_execution"
                )
            scope_id = execution.lineage.scope_id
            execution.register_resource(_NestedCallResource(self, parent_id, scope_id))
            self._nested_active[parent_id] = scope_id
        return ToolDecision.ALLOW


@dataclass(frozen=True)
class PoseTolerance:
    position: float = 0.01
    orientation: float = 0.05
    maximum_age_ns: int = 1_000_000_000

    def __post_init__(self):
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in (self.position, self.orientation)
        ):
            raise ValueError("Pose tolerances must be finite and non-negative.")
        if type(self.maximum_age_ns) is not int or self.maximum_age_ns < 0:
            raise ValueError("Maximum pose age must be non-negative.")


def pose_compatible(first, second, tolerance: PoseTolerance) -> bool:
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return False
    frame = first.get("frame_id")
    if not isinstance(frame, str) or not frame or frame != second.get("frame_id"):
        return False
    try:
        a, b = first["xyz"], second["xyz"]
        qa, qb = first["quaternion"], second["quaternion"]
        if len(a) != 3 or len(b) != 3 or len(qa) != 4 or len(qb) != 4:
            return False
        if not all(
            type(value) in (int, float) and math.isfinite(value)
            for value in (*a, *b, *qa, *qb)
        ):
            return False
        if any(abs(sum(value * value for value in q) - 1) > 1e-5 for q in (qa, qb)):
            return False
        distance = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))
        angle = 2 * math.acos(
            min(1.0, abs(sum(x * y for x, y in zip(qa, qb, strict=True))))
        )
        return distance <= tolerance.position and angle <= tolerance.orientation
    except (KeyError, TypeError, ValueError):
        return False


def _supports(entry, refs) -> bool:
    return bool(refs) and set(refs) <= {ref.id for ref in entry.supporting_claims}


async def validate_decision(
    world: World,
    context: ToolContext,
    arguments,
    roles: Mapping[str, tuple[str, str]],
    tolerance: PoseTolerance,
    *,
    scope: str | None,
    clock_resolver: ClockResolver,
    policy: PhysicalPolicy,
) -> WorldSnapshot:
    """Reject before dispatch; never change the Agent's target or refresh its source."""
    execution = context.execution
    if (
        not isinstance(execution, RuntimeToolExecutionContext)
        or context.message_id is None
    ):
        reject("decision_context_missing")
    if not policy.claim_turn_action(context):
        reject("fresh_decision_required")
    return await revalidate_decision(
        world,
        context,
        arguments,
        roles,
        tolerance,
        scope=scope,
        clock_resolver=clock_resolver,
        policy=policy,
    )


async def revalidate_decision(
    world: World,
    context: ToolContext,
    arguments,
    roles: Mapping[str, tuple[str, str]],
    tolerance: PoseTolerance,
    *,
    scope: str | None,
    clock_resolver: ClockResolver,
    policy: PhysicalPolicy,
) -> WorldSnapshot:
    """Recheck the consumed decision immediately before Host dispatch.

    This preserves the original source Snapshot and action slot across awaited
    preflight work; it never turns refreshed evidence into a new Model decision.
    """
    execution = context.execution
    if not isinstance(
        execution, RuntimeToolExecutionContext
    ) or not policy.owns_turn_action(context):
        reject("fresh_decision_required")
    source = context.decision_world_snapshot
    if source is None:
        reject("decision_context_missing")
    if source.world_id != world.world_id:
        reject("world_mismatch")
    evidence = arguments.get("decision_evidence")
    if not isinstance(evidence, Mapping):
        reject("decision_evidence_missing")
    selection = evidence.get("selection")
    if not isinstance(selection, Mapping) or not selection.get("filters"):
        reject("decision_evidence_incomplete")
    if selection.get("scope") != scope:
        reject("decision_scope_mismatch")
    current = await world.snapshot()
    target = arguments["target_entity"]
    dependencies = []
    predicates = set()
    for item in selection["filters"]:
        predicate = item["predicate"]
        if predicate in predicates:
            reject("duplicate_search_predicate")
        predicates.add(predicate)
        key = WorldEntryKey(EntityRef(target), predicate, selection.get("scope"))
        entry = source.lookup(key)
        value = (
            EntityRef(item["entity_id"])
            if item["value_kind"] == "entity_ref"
            else item["value"]
        )
        if entry is None or entry.status is not WorldStatus.KNOWN:
            reject("decision_source_not_ready")
        if canonical_json_digest(_value_data(entry.value)) != canonical_json_digest(
            _value_data(value)
        ):
            reject("decision_evidence_mismatch")
        if not _supports(entry, selection.get("claim_refs", ())):
            # Selection includes several predicates; validate each predicate's support separately.
            refs = set(selection.get("claim_refs", ()))
            if not refs.intersection(ref.id for ref in entry.supporting_claims):
                reject("decision_evidence_invalid")
        dependencies.append((key, entry))
    valid_refs = {
        ref.id for _, entry in dependencies for ref in entry.supporting_claims
    }
    if not set(selection.get("claim_refs", ())) <= valid_refs:
        reject("decision_evidence_invalid")
    readiness = evidence.get("readiness", ())
    by_role = {item["role"]: item for item in readiness}
    if len(by_role) != len(readiness) or set(roles) != set(by_role):
        reject("decision_evidence_incomplete")
    for role, (subject, predicate) in roles.items():
        item = by_role[role]
        if item.get("subject") != subject or item.get("predicate") != predicate:
            reject("decision_evidence_mismatch")
        if item.get("scope") != scope:
            reject("decision_scope_mismatch")
        key = WorldEntryKey(EntityRef(subject), predicate, scope)
        entry = source.lookup(key)
        if entry is None or entry.status is not WorldStatus.KNOWN:
            reject("decision_source_not_ready")
        if role == "holding" and entry.value != EntityRef(target):
            reject("decision_source_not_ready")
        if not _supports(entry, item.get("claim_refs", ())):
            reject("decision_evidence_invalid")
        dependencies.append((key, entry))
    for key, before in dependencies:
        after = current.lookup(key)
        if after is None or after.status is not WorldStatus.KNOWN:
            reject("decision_evidence_uncertain")
        compatible = (
            pose_compatible(before.value, after.value, tolerance)
            if key.predicate == "pose"
            else canonical_json_digest(_value_data(before.value))
            == canonical_json_digest(_value_data(after.value))
        )
        if not compatible:
            reject("decision_evidence_changed")
        if key.predicate == "pose":
            if after.valid_at is None:
                reject("pose_age_unavailable")
            now = clock_resolver.now(after.valid_at.clock_id)
            if now is None or now.clock_id != after.valid_at.clock_id:
                reject("pose_age_unavailable")
            age = now.nanoseconds - after.valid_at.nanoseconds
            if not 0 <= age <= tolerance.maximum_age_ns:
                reject("pose_stale")
    if not policy.owns_turn_action(context):
        reject("fresh_decision_required")
    return current


def _value_data(value):
    return (
        {"kind": "entity_ref", "id": value.id}
        if isinstance(value, EntityRef)
        else {"kind": "json", "value": value}
    )


def decision_evidence(
    snapshot: WorldSnapshot,
    entity: str,
    predicates=("category", "color"),
    roles=(),
    *,
    scope="workspace",
):
    """Build explicit evidence for deterministic reference Models and tests."""
    filters = []
    refs: list[str] = []
    readiness = []
    for predicate in predicates:
        entry = snapshot.lookup(WorldEntryKey(EntityRef(entity), predicate, scope))
        if entry is None or entry.status is not WorldStatus.KNOWN:
            raise ValueError("Selection evidence is not ready.")
        item = {"predicate": predicate, "value_kind": "json", "value": entry.value}
        if isinstance(entry.value, EntityRef):
            item = {
                "predicate": predicate,
                "value_kind": "entity_ref",
                "entity_id": entry.value.id,
            }
        filters.append(item)
        refs.extend(ref.id for ref in entry.supporting_claims)
    for role, subject, predicate in roles:
        entry = snapshot.lookup(WorldEntryKey(EntityRef(subject), predicate, scope))
        if entry is None or entry.status is not WorldStatus.KNOWN:
            raise ValueError("Readiness evidence is not ready.")
        readiness.append(
            {
                "role": role,
                "subject": subject,
                "predicate": predicate,
                "scope": scope,
                "claim_refs": [ref.id for ref in entry.supporting_claims],
            }
        )
    return {
        "selection": {
            "scope": scope,
            "filters": filters,
            "claim_refs": sorted(set(refs)),
        },
        "readiness": readiness,
    }


def decision_evidence_schema():
    """Closed JSON structure; each physical Tool still validates its required roles."""
    text = {"type": "string", "minLength": 1}
    references = {"type": "array", "minItems": 1, "uniqueItems": True, "items": text}
    scope = {"type": ["string", "null"], "minLength": 1}
    json_filter = {
        "type": "object",
        "properties": {"predicate": text, "value_kind": {"const": "json"}, "value": {}},
        "required": ["predicate", "value_kind", "value"],
        "additionalProperties": False,
    }
    entity_filter = {
        "type": "object",
        "properties": {
            "predicate": text,
            "value_kind": {"const": "entity_ref"},
            "entity_id": {**text, "x-world-entity-ref": True},
        },
        "required": ["predicate", "value_kind", "entity_id"],
        "additionalProperties": False,
    }
    selection = {
        "type": "object",
        "properties": {
            "scope": scope,
            "filters": {
                "type": "array",
                "minItems": 1,
                "items": {"oneOf": [json_filter, entity_filter]},
            },
            "claim_refs": references,
        },
        "required": ["scope", "filters", "claim_refs"],
        "additionalProperties": False,
    }
    readiness = {
        "type": "object",
        "properties": {
            "role": text,
            "subject": {**text, "x-world-entity-ref": True},
            "predicate": text,
            "scope": scope,
            "claim_refs": references,
        },
        "required": ["role", "subject", "predicate", "scope", "claim_refs"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "selection": selection,
            "readiness": {"type": "array", "items": readiness},
        },
        "required": ["selection", "readiness"],
        "additionalProperties": False,
    }
