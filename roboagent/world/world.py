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
    freeze_json_object,
)
from ._codec import world_data
from .types import (
    Claim,
    ClaimKind,
    ClaimRef,
    ClockResolver,
    EntityCandidate,
    EntityRef,
    EntitySearchResult,
    Observation,
    ObservationRef,
    ObservationTime,
    ResourceRef,
    WorldAnswer,
    WorldCandidate,
    WorldEntry,
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
        groups: dict[tuple[EntityRef, str], list[Claim]] = defaultdict(list)
        for claim in claims:
            groups[claim.subject, claim.predicate].append(claim)
        updated = {
            (claim.subject, claim.predicate): index
            for index, claim in enumerate(claims)
        }
        entries = tuple(
            _resolve(
                subject,
                predicate,
                [claim for claim in group if claim.ref not in superseded],
                times,
                grounded,
            )
            for (subject, predicate), group in sorted(
                groups.items(),
                key=lambda item: (updated[item[0]], item[0][0].id, item[0][1]),
            )
        )
        return WorldSnapshot(
            self.world_id, revision, datetime.now(timezone.utc), entries
        )

    async def query(self, entity: EntityRef, predicate: str) -> WorldAnswer:
        if (
            not isinstance(entity, EntityRef)
            or not isinstance(predicate, str)
            or not predicate.strip()
        ):
            raise WorldError("invalid_claim")
        snapshot = await self.snapshot()
        entry = next(
            (
                item
                for item in snapshot.entries
                if item.subject == entity and item.predicate == predicate
            ),
            None,
        )
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
        self, filters: Mapping[str, JsonValue], *, limit: int = 16
    ) -> EntitySearchResult:
        if (
            type(limit) is not int
            or limit < 1
            or limit > self.limits.max_search_results
        ):
            raise WorldError("invalid_claim")
        if not isinstance(filters, Mapping):
            raise WorldError("invalid_claim")
        try:
            frozen = freeze_json_object(filters)
        except (TypeError, ValueError, RecursionError) as exc:
            raise WorldError("invalid_claim") from exc
        snapshot = await self.snapshot()
        entities: dict[EntityRef, dict[str, WorldEntry]] = defaultdict(dict)
        for entry in snapshot.entries:
            entities[entry.subject][entry.predicate] = entry
        candidates = []
        for entity, attributes in sorted(entities.items(), key=lambda item: item[0].id):
            if all(
                key in attributes
                and attributes[key].status is WorldStatus.KNOWN
                and _value_key(attributes[key].value) == _value_key(value)
                for key, value in frozen.items()
            ):
                refs = tuple(
                    dict.fromkeys(
                        ref
                        for key in frozen
                        for ref in attributes[key].supporting_claims
                    )
                )
                candidates.append(EntityCandidate(entity, frozen, refs))
        return EntitySearchResult(
            self.world_id,
            snapshot.revision,
            tuple(candidates[:limit]),
            len(candidates) > limit,
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


def _resolve(
    subject: EntityRef,
    predicate: str,
    claims: list[Claim],
    times: Mapping[str, ObservationTime | None],
    grounded: set[ClaimRef],
) -> WorldEntry:
    eligible = [claim for claim in claims if claim.ref in grounded]
    # Only compare time inside one scope and clock domain. An old
    # cloud interpretation cannot win merely because it arrived last.
    latest: dict[tuple[str | None, str], int] = {}
    for claim in eligible:
        if claim.valid_at is not None:
            key = (claim.scope, claim.valid_at.clock_id)
            latest[key] = max(latest.get(key, -1), claim.valid_at.nanoseconds)
    selected = [
        claim
        for claim in eligible
        if claim.valid_at is None
        or claim.valid_at.nanoseconds == latest[claim.scope, claim.valid_at.clock_id]
    ]
    live, expired, uncertain = [], [], []
    for claim in selected:
        now = times.get(claim.valid_at.clock_id) if claim.valid_at is not None else None
        if (
            claim.valid_at is not None
            and now is not None
            and now.nanoseconds < claim.valid_at.nanoseconds
        ):
            uncertain.append(claim)
        elif claim.valid_for_ns is None:
            live.append(claim)
        else:
            assert claim.valid_at is not None
            if now is None:
                uncertain.append(claim)
            elif now.nanoseconds >= claim.valid_at.nanoseconds + claim.valid_for_ns:
                expired.append(claim)
            else:
                live.append(claim)
    basis = live or expired or uncertain or claims
    candidates = tuple(
        WorldCandidate(claim.value, (claim.ref,), claim.evidence_refs, claim.valid_at)
        for claim in sorted(basis, key=lambda claim: claim.ref.id)
    )
    values = {_value_key(claim.value) for claim in basis}
    # Different scopes are alternatives, not evidence of a contradiction.
    if not eligible or (not live and uncertain):
        status = WorldStatus.UNKNOWN
    elif len(values) > 1:
        scope_values: dict[str | None, set[str]] = defaultdict(set)
        for claim in live:
            scope_values[claim.scope].add(_value_key(claim.value))
        # A claim in another scope cannot erase a conflict already established
        # inside one scope. Cross-scope alternatives alone remain UNKNOWN.
        conflicted = any(len(items) > 1 for items in scope_values.values())
        status = WorldStatus.CONFLICTED if conflicted else WorldStatus.UNKNOWN
    else:
        status = WorldStatus.KNOWN if live else WorldStatus.STALE
    certain = status in (WorldStatus.KNOWN, WorldStatus.STALE)
    refs = tuple(claim.ref for claim in basis)
    evidence = tuple(dict.fromkeys(e for claim in basis for e in claim.evidence_refs))
    valid_times = {claim.valid_at for claim in basis}
    return WorldEntry(
        subject,
        predicate,
        status,
        basis[0].value if certain else None,
        () if certain else candidates,
        refs,
        evidence,
        next(iter(valid_times)) if len(valid_times) == 1 else None,
    )
