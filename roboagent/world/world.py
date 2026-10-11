"""Bounded, transactional cognition on one asyncio event loop.

Capacity is rejected rather than evicted: every retained claim keeps its full
provenance, including correction chains. Large media is owned by the Host.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from roboagent.message import (
    JsonValue,
    canonical_json_dumps,
    canonical_json_digest,
    freeze_json,
)
from ._codec import world_data
from .types import (
    Claim,
    ClaimKind,
    ClaimRef,
    ClockResolver,
    EntityCandidate,
    EntityMatchStatus,
    EntityRef,
    EntitySearchResult,
    Observation,
    ObservationRef,
    ObservationTime,
    ResourceRef,
    WorldAnswer,
    WorldCandidate,
    WorldEntry,
    WorldEntryKey,
    WorldError,
    WorldLimits,
    WorldReceipt,
    WorldSnapshot,
    WorldStatus,
)


class World:
    def __init__(
        self,
        world_id: str,
        *,
        limits: WorldLimits | None = None,
        clock_resolver: ClockResolver | None = None,
        allowed_sources: Sequence[str] = (),
        allowed_scopes: Sequence[str | None] = (None,),
    ) -> None:
        if (
            not isinstance(world_id, str)
            or not world_id.strip()
            or len(world_id) > 1024
        ):
            raise ValueError("world_id must be non-empty.")
        if limits is not None and not isinstance(limits, WorldLimits):
            raise TypeError("limits must be WorldLimits.")
        if clock_resolver is not None and not callable(
            getattr(clock_resolver, "now", None)
        ):
            raise TypeError("clock_resolver must implement now().")
        if any(
            not isinstance(source, str) or not source.strip()
            for source in allowed_sources
        ):
            raise ValueError("allowed_sources must contain non-empty identities.")
        if any(
            scope is not None and (not isinstance(scope, str) or not scope.strip())
            for scope in allowed_scopes
        ):
            raise ValueError("allowed_scopes must contain scopes or None.")
        self._world_id = world_id
        self._limits = limits or WorldLimits()
        self._clock = clock_resolver
        self._sources = frozenset(allowed_sources)
        self._scopes = frozenset(allowed_scopes)
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._revision = 0
        self._observations: dict[ObservationRef, Observation] = {}
        self._transactions: dict[ObservationRef, frozenset[ClaimRef]] = {}
        self._claims: dict[ClaimRef, Claim] = {}
        self._payload_bytes = 0
        self._claim_digests: dict[ClaimRef, str] = {}
        self._observation_digests: dict[ObservationRef, str] = {}

    @property
    def world_id(self) -> str:
        return self._world_id

    @property
    def limits(self) -> WorldLimits:
        return self._limits

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("World must be used on its owning event loop.")

    def _membership(self, source: str, scope: str | None) -> None:
        if source not in self._sources:
            raise WorldError("source_not_allowed")
        if scope not in self._scopes:
            raise WorldError("scope_not_allowed")

    async def observe(
        self, observation: Observation, *, claims: Sequence[Claim] = ()
    ) -> WorldReceipt:
        if not isinstance(observation, Observation):
            raise WorldError("invalid_observation")
        return await self._commit(observation, claims)

    async def add_claims(self, claims: Sequence[Claim]) -> WorldReceipt:
        return await self._commit(None, claims)

    async def _commit(
        self, observation: Observation | None, claims: Sequence[Claim]
    ) -> WorldReceipt:
        self._check_loop()
        if not isinstance(claims, Sequence):
            raise WorldError("invalid_claim")
        if len(claims) > self.limits.max_claims:
            raise WorldError("world_capacity_exceeded")
        batch = tuple(claims)
        if observation is not None:
            if not isinstance(observation, Observation):
                raise WorldError("invalid_observation")
            self._membership(observation.ref.source, observation.scope)
        incoming: dict[ClaimRef, Claim] = {}
        incoming_digests: dict[ClaimRef, str] = {}
        for claim in batch:
            if not isinstance(claim, Claim):
                raise WorldError("invalid_claim")
            self._membership(claim.source, claim.scope)
            digest = canonical_json_digest(world_data(claim))
            if claim.ref in incoming and incoming_digests[claim.ref] != digest:
                raise WorldError("claim_conflict")
            incoming[claim.ref] = claim
            incoming_digests[claim.ref] = digest
        # Size work happens outside the short commit section.
        claim_sizes = {
            ref: len(canonical_json_dumps(world_data(claim)).encode())
            for ref, claim in incoming.items()
        }
        observation_size = (
            0
            if observation is None
            else len(canonical_json_dumps(world_data(observation)).encode())
        )
        if any(
            size > self.limits.max_payload_bytes
            for size in (*claim_sizes.values(), observation_size)
        ):
            raise WorldError("world_capacity_exceeded")
        observation_digest = (
            None
            if observation is None
            else canonical_json_digest(world_data(observation))
        )
        async with self._lock:
            observations = self._observations.copy()
            existing_observation = None
            if observation is not None:
                existing_observation = observations.get(observation.ref)
                if existing_observation is not None:
                    if self._observation_digests[
                        observation.ref
                    ] != observation_digest or self._transactions[
                        observation.ref
                    ] != frozenset(incoming):
                        raise WorldError("observation_conflict")
                observations[observation.ref] = observation
            merged = self._claims.copy()
            new_claims = {}
            for ref, claim in incoming.items():
                if ref in merged and self._claim_digests[ref] != incoming_digests[ref]:
                    raise WorldError("claim_conflict")
                if ref not in merged:
                    new_claims[ref] = claim
                merged[ref] = claim
            for claim in incoming.values():
                if (
                    claim.kind
                    in (
                        ClaimKind.MEASUREMENT,
                        ClaimKind.INTERPRETATION,
                        ClaimKind.INFERENCE,
                    )
                    and not claim.evidence_refs
                ):
                    raise WorldError("invalid_claim")
                for evidence in claim.evidence_refs:
                    if evidence.kind == "observation":
                        if (
                            ObservationRef(evidence.source or "", evidence.id)
                            not in observations
                        ):
                            raise WorldError("evidence_not_found")
                    else:
                        parent = merged.get(ClaimRef(evidence.id))
                        if parent is None or (
                            evidence.source is not None
                            and evidence.source != parent.source
                        ):
                            raise WorldError("evidence_not_found")
                for replaced in claim.supersedes:
                    previous = merged.get(replaced)
                    if previous is None:
                        raise WorldError("evidence_not_found")
            self._validate_graph(merged, set(self._claims))
            entities = {claim.subject for claim in merged.values()}
            entities.update(
                claim.value
                for claim in merged.values()
                if isinstance(claim.value, EntityRef)
            )
            media_count = sum(
                isinstance(item.payload, ResourceRef) for item in observations.values()
            )
            payload_bytes = self._payload_bytes + sum(
                claim_sizes[ref] for ref in new_claims
            )
            if observation is not None and existing_observation is None:
                payload_bytes += observation_size
            counts = (
                (len(entities), self.limits.max_entities),
                (len(observations), self.limits.max_observations),
                (len(merged), self.limits.max_claims),
                (len(observations) + len(merged), self.limits.max_evidence_metadata),
                (media_count, self.limits.max_media_refs),
                (payload_bytes, self.limits.max_payload_bytes),
            )
            if any(count > limit for count, limit in counts):
                raise WorldError("world_capacity_exceeded")
            changed = bool(new_claims) or (
                observation is not None and existing_observation is None
            )
            if changed:
                self._observations = observations
                self._claims = merged
                self._payload_bytes = payload_bytes
                self._claim_digests.update(incoming_digests)
                if observation is not None:
                    assert observation_digest is not None
                    self._observation_digests[observation.ref] = observation_digest
                    self._transactions[observation.ref] = frozenset(incoming)
                self._revision += 1
            return WorldReceipt(
                self.world_id,
                self._revision,
                changed,
                observation.ref if observation else None,
                tuple(incoming),
            )

    @staticmethod
    def _validate_graph(
        claims: Mapping[ClaimRef, Claim], existing: set[ClaimRef]
    ) -> None:
        # Iterative DFS avoids recursion failures on bounded but long histories.
        done: set[ClaimRef] = existing.copy()
        active: set[ClaimRef] = set()
        for root in claims:
            stack = [(root, False)]
            while stack:
                ref, leaving = stack.pop()
                if leaving:
                    active.remove(ref)
                    done.add(ref)
                elif ref not in done:
                    if ref in active:
                        raise WorldError("invalid_claim")
                    active.add(ref)
                    stack.append((ref, True))
                    claim = claims[ref]
                    parents = (
                        *claim.supersedes,
                        *(
                            ClaimRef(e.id)
                            for e in claim.evidence_refs
                            if e.kind == "claim"
                        ),
                    )
                    stack.extend((parent, False) for parent in parents)

    async def snapshot(self) -> WorldSnapshot:
        self._check_loop()
        async with self._lock:
            revision = self._revision
            claims = tuple(self._claims.values())
        # No await after capture: one evaluation time per clock domain, with no
        # network, parsing, media access or serialization under the lock.
        times: dict[str, ObservationTime | None] = {}
        for claim in claims:
            if claim.valid_at is not None and claim.valid_at.clock_id not in times:
                clock_id = claim.valid_at.clock_id
                try:
                    now = self._clock.now(clock_id) if self._clock is not None else None
                except Exception as exc:
                    raise WorldError("clock_unavailable") from exc
                if now is not None and (
                    not isinstance(now, ObservationTime) or now.clock_id != clock_id
                ):
                    raise WorldError("clock_unavailable")
                times[clock_id] = now
        superseded = {ref for claim in claims for ref in claim.supersedes}
        grounded = _grounded_claims(claims, superseded)
        groups: dict[tuple[EntityRef, str, str | None], list[Claim]] = defaultdict(list)
        for claim in claims:
            groups[claim.subject, claim.predicate, claim.scope].append(claim)
        updated = {
            (claim.subject, claim.predicate, claim.scope): index
            for index, claim in enumerate(claims)
        }
        entries = tuple(
            _resolve(
                subject,
                predicate,
                scope,
                [claim for claim in group if claim.ref not in superseded],
                times,
                grounded,
            )
            for (subject, predicate, scope), group in sorted(
                groups.items(),
                key=lambda item: (updated[item[0]], item[0][0].id, item[0][1]),
            )
        )
        return WorldSnapshot(
            self.world_id, revision, datetime.now(timezone.utc), entries
        )

    async def query(
        self, entity: EntityRef, predicate: str, *, scope: str | None = None
    ) -> WorldAnswer:
        if (
            not isinstance(entity, EntityRef)
            or not isinstance(predicate, str)
            or not predicate.strip()
        ):
            raise WorldError("invalid_claim")
        snapshot = await self.snapshot()
        entry = snapshot.lookup(WorldEntryKey(entity, predicate, scope))
        if entry is None:
            return WorldAnswer(
                self.world_id,
                snapshot.revision,
                WorldStatus.UNKNOWN,
                None,
                (),
                (),
                (),
                None,
            )
        return WorldAnswer(
            self.world_id,
            snapshot.revision,
            entry.status,
            entry.value,
            entry.candidates,
            entry.supporting_claims,
            entry.evidence_refs,
            entry.valid_at,
        )

    async def find_entities(
        self,
        filters: Mapping[str, JsonValue | EntityRef],
        *,
        scope: str | None = None,
        include_possible: bool = False,
        limit: int = 16,
    ) -> EntitySearchResult:
        if type(limit) is not int or not 1 <= limit <= self.limits.max_search_results:
            raise WorldError("invalid_claim")
        if (
            not isinstance(filters, Mapping)
            or not filters
            or type(include_possible) is not bool
        ):
            raise WorldError("invalid_claim")
        if scope is not None and (not isinstance(scope, str) or not scope.strip()):
            raise WorldError("invalid_claim")
        frozen = {}
        try:
            for predicate, value in filters.items():
                WorldEntryKey(EntityRef("filter"), predicate, scope)
                frozen[predicate] = (
                    value if isinstance(value, EntityRef) else freeze_json(value)
                )
        except (TypeError, ValueError, RecursionError) as exc:
            raise WorldError("invalid_claim") from exc
        snapshot = await self.snapshot()
        entities: dict[EntityRef, dict[str, WorldEntry]] = defaultdict(dict)
        for snapshot_entry in snapshot.entries:
            if snapshot_entry.scope == scope:
                entities[snapshot_entry.subject][snapshot_entry.predicate] = (
                    snapshot_entry
                )
        candidates = []
        for entity, attributes in entities.items():
            matched: list[str] = []
            uncertain: list[str] = []
            missing: list[str] = []
            refs: set[ClaimRef] = set()
            excluded = False
            for predicate, value in sorted(frozen.items()):
                entry = attributes.get(predicate)
                if entry is None or (
                    entry.status is WorldStatus.UNKNOWN and not entry.candidates
                ):
                    missing.append(predicate)
                elif entry.status is WorldStatus.KNOWN:
                    if _value_key(entry.value) != _value_key(value):
                        excluded = True
                        break
                    matched.append(predicate)
                    refs.update(entry.supporting_claims)
                else:
                    supports = [
                        item
                        for item in entry.candidates
                        if _value_key(item.value) == _value_key(value)
                    ]
                    if not supports:
                        excluded = True
                        break
                    uncertain.append(predicate)
                    refs.update(
                        ref for item in supports for ref in item.supporting_claims
                    )
            if excluded or not (matched or uncertain):
                continue
            status = (
                EntityMatchStatus.POSSIBLE
                if uncertain or missing
                else EntityMatchStatus.CONFIRMED
            )
            if status is EntityMatchStatus.POSSIBLE and not include_possible:
                continue
            candidates.append(
                EntityCandidate(
                    entity,
                    scope,
                    status,
                    tuple(matched),
                    tuple(uncertain),
                    tuple(missing),
                    tuple(sorted(refs, key=lambda ref: ref.id)),
                )
            )
        candidates.sort(
            key=lambda item: (item.status is EntityMatchStatus.POSSIBLE, item.entity.id)
        )
        return EntitySearchResult(
            self.world_id,
            snapshot.revision,
            tuple(candidates[:limit]),
            len(candidates) > limit,
            len(candidates),
        )


def _value_key(value: object) -> str:
    if isinstance(value, EntityRef):
        return "entity:" + value.id
    return "json:" + canonical_json_digest(value)


def _grounded_claims(
    claims: tuple[Claim, ...], superseded: set[ClaimRef]
) -> set[ClaimRef]:
    """A hypothesis/prior/prediction cannot become KNOWN through an inference.

    Traverse the already validated DAG without recursive Python calls. World
    checks evidence support, never interprets the observation payload.
    """
    indexed = {claim.ref: claim for claim in claims}
    supported: set[ClaimRef] = set()
    done: set[ClaimRef] = set()
    for root in indexed:
        stack = [(root, False)]
        while stack:
            ref, leaving = stack.pop()
            if ref in done:
                continue
            claim = indexed[ref]
            if leaving:
                if (
                    ref not in superseded
                    and claim.kind
                    in (
                        ClaimKind.MEASUREMENT,
                        ClaimKind.INTERPRETATION,
                        ClaimKind.INFERENCE,
                    )
                    and any(
                        evidence.kind == "observation"
                        or ClaimRef(evidence.id) in supported
                        for evidence in claim.evidence_refs
                    )
                ):
                    supported.add(ref)
                done.add(ref)
            else:
                stack.append((ref, True))
                stack.extend(
                    (ClaimRef(evidence.id), False)
                    for evidence in claim.evidence_refs
                    if evidence.kind == "claim"
                )
    return supported


def _temporal_class(claim: Claim, times: Mapping[str, ObservationTime | None]) -> str:
    now = times.get(claim.valid_at.clock_id) if claim.valid_at is not None else None
    if (
        claim.valid_at is not None
        and now is not None
        and now.nanoseconds < claim.valid_at.nanoseconds
    ):
        return "unresolved"
    if claim.valid_at is not None and now is None:
        return "unresolved"
    if claim.valid_for_ns is None:
        return "live"
    if now is None:
        return "unresolved"
    assert claim.valid_at is not None
    return (
        "expired"
        if now.nanoseconds >= claim.valid_at.nanoseconds + claim.valid_for_ns
        else "live"
    )


def _resolve(
    subject: EntityRef,
    predicate: str,
    scope: str | None,
    claims: list[Claim],
    times: Mapping[str, ObservationTime | None],
    grounded: set[ClaimRef],
) -> WorldEntry:
    classified = [(claim, _temporal_class(claim, times)) for claim in claims]
    heads: dict[tuple[str, bool, str], int] = {}
    for claim, temporal in classified:
        if claim.valid_at is not None and temporal != "unresolved":
            key = (claim.source, claim.ref in grounded, claim.valid_at.clock_id)
            heads[key] = max(heads.get(key, -1), claim.valid_at.nanoseconds)
    selected = [
        (claim, temporal)
        for claim, temporal in classified
        if claim.valid_at is None
        or temporal == "unresolved"
        or claim.valid_at.nanoseconds
        == heads[claim.source, claim.ref in grounded, claim.valid_at.clock_id]
    ]
    live = [
        claim
        for claim, temporal in selected
        if claim.ref in grounded and temporal == "live"
    ]
    expired = [
        claim
        for claim, temporal in selected
        if claim.ref in grounded and temporal == "expired"
    ]
    unresolved = [
        claim
        for claim, temporal in selected
        if claim.ref in grounded and temporal == "unresolved"
    ]
    ungrounded = [claim for claim, _ in selected if claim.ref not in grounded]
    live_values = {_value_key(claim.value) for claim in live}
    value = None
    if len(live_values) > 1:
        status, basis = WorldStatus.CONFLICTED, live + unresolved
    elif live:
        if any(_value_key(claim.value) not in live_values for claim in unresolved):
            status, basis = WorldStatus.UNKNOWN, live + unresolved
        else:
            status, basis, value = WorldStatus.KNOWN, live, live[0].value
    elif unresolved:
        status, basis = WorldStatus.UNKNOWN, expired + unresolved + ungrounded
    elif expired:
        status, basis = WorldStatus.STALE, expired
    else:
        status, basis = WorldStatus.UNKNOWN, ungrounded
    groups: dict[str, list[Claim]] = defaultdict(list)
    for claim in basis:
        groups[_value_key(claim.value)].append(claim)
    candidates = []
    for _, supports in sorted(groups.items()):
        refs, evidence, valid_at = _provenance(supports)
        candidates.append(WorldCandidate(supports[0].value, refs, evidence, valid_at))
    refs, evidence, valid_at = _provenance(basis)
    return WorldEntry(
        subject,
        predicate,
        status,
        value,
        tuple(candidates),
        refs,
        evidence,
        valid_at,
        scope,
    )


def _provenance(claims: list[Claim]):
    refs = tuple(sorted({claim.ref for claim in claims}, key=lambda ref: ref.id))
    evidence = tuple(
        sorted(
            {item for claim in claims for item in claim.evidence_refs},
            key=lambda item: (item.kind, item.source or "", item.id),
        )
    )
    times = {claim.valid_at for claim in claims}
    return refs, evidence, next(iter(times)) if len(times) == 1 else None
