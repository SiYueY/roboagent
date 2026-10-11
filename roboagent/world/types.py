"""Immutable environment evidence and cognition contracts.

Source identities describe provenance. Hosts authenticate writers before calling
World; these values are not credentials or physical ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

from roboagent.message import (
    FrozenJsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
)


class WorldError(ValueError):
    """Stable public error code without resource contents in the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object, code: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise WorldError(code)


def _time(value: datetime, code: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise WorldError(code)


@dataclass(frozen=True, slots=True)
class ObservationTime:
    clock_id: str
    nanoseconds: int

    def __post_init__(self) -> None:
        _text(self.clock_id, "clock_unavailable")
        if type(self.nanoseconds) is not int or self.nanoseconds < 0:
            raise WorldError("clock_unavailable")


class ClockResolver(Protocol):
    def now(self, clock_id: str) -> ObservationTime | None: ...


@dataclass(frozen=True, slots=True)
class ObservationRef:
    source: str
    id: str

    def __post_init__(self) -> None:
        _text(self.source, "invalid_observation")
        _text(self.id, "invalid_observation")


@dataclass(frozen=True, slots=True)
class EntityRef:
    id: str

    def __post_init__(self) -> None:
        _text(self.id, "invalid_claim")


@dataclass(frozen=True, slots=True)
class ClaimRef:
    id: str

    def __post_init__(self) -> None:
        _text(self.id, "invalid_claim")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: Literal["observation", "claim"]
    source: str | None
    id: str

    def __post_init__(self) -> None:
        _text(self.id, "invalid_claim")
        if self.kind not in ("observation", "claim"):
            raise WorldError("invalid_claim")
        if self.kind == "observation":
            _text(self.source, "invalid_claim")
        elif self.source is not None:
            _text(self.source, "invalid_claim")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    uri: str
    media_type: str | None = None
    size: int | None = None
    digest: str | None = None
    available: bool = True

    def __post_init__(self) -> None:
        _text(self.uri, "invalid_observation")
        for value in (self.media_type, self.digest):
            if value is not None:
                _text(value, "invalid_observation")
        if self.size is not None and (type(self.size) is not int or self.size < 0):
            raise WorldError("invalid_observation")
        if type(self.available) is not bool:
            raise WorldError("invalid_observation")


@dataclass(frozen=True, slots=True)
class Observation:
    ref: ObservationRef
    modality: str
    observed_at: ObservationTime
    received_at: datetime
    payload: JsonValue | ResourceRef
    scope: str | None = None

    def __post_init__(self) -> None:
        _text(self.modality, "invalid_observation")
        _time(self.received_at, "invalid_observation")
        if not isinstance(self.ref, ObservationRef) or not isinstance(
            self.observed_at, ObservationTime
        ):
            raise WorldError("invalid_observation")
        if self.scope is not None:
            _text(self.scope, "invalid_observation")
        if not isinstance(self.payload, ResourceRef):
            try:
                object.__setattr__(self, "payload", freeze_json(self.payload))
            except (ValueError, TypeError, RecursionError) as exc:
                raise WorldError("invalid_observation") from exc


class ClaimKind(str, Enum):
    MEASUREMENT = "measurement"
    INTERPRETATION = "interpretation"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    PRIOR = "prior"
    PREDICTION = "prediction"


@dataclass(frozen=True, slots=True)
class Claim:
    ref: ClaimRef
    subject: EntityRef
    predicate: str
    value: JsonValue | EntityRef
    kind: ClaimKind
    source: str
    scope: str | None
    evidence_refs: tuple[EvidenceRef, ...]
    asserted_at: datetime
    valid_at: ObservationTime | None = None
    valid_for_ns: int | None = None
    supersedes: tuple[ClaimRef, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.predicate, self.source):
            _text(value, "invalid_claim")
        _time(self.asserted_at, "invalid_claim")
        if (
            not isinstance(self.ref, ClaimRef)
            or not isinstance(self.subject, EntityRef)
            or not isinstance(self.kind, ClaimKind)
        ):
            raise WorldError("invalid_claim")
        if self.scope is not None:
            _text(self.scope, "invalid_claim")
        if self.valid_at is not None and not isinstance(self.valid_at, ObservationTime):
            raise WorldError("invalid_claim")
        if self.valid_for_ns is not None and (
            type(self.valid_for_ns) is not int
            or self.valid_for_ns < 0
            or self.valid_at is None
        ):
            raise WorldError("invalid_claim")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        if not all(
            isinstance(item, EvidenceRef) for item in self.evidence_refs
        ) or not all(isinstance(item, ClaimRef) for item in self.supersedes):
            raise WorldError("invalid_claim")
        if self.ref in self.supersedes:
            raise WorldError("invalid_claim")
        if not isinstance(self.value, EntityRef):
            try:
                object.__setattr__(self, "value", freeze_json(self.value))
            except (ValueError, TypeError, RecursionError) as exc:
                raise WorldError("invalid_claim") from exc


class WorldStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    STALE = "stale"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class WorldCandidate:
    value: JsonValue | EntityRef
    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    valid_at: ObservationTime | None

    def __post_init__(self) -> None:
        _cognition(self)


@dataclass(frozen=True, slots=True)
class WorldEntry:
    subject: EntityRef
    predicate: str
    status: WorldStatus
    value: JsonValue | EntityRef | None
    candidates: tuple[WorldCandidate, ...]
    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    valid_at: ObservationTime | None

    def __post_init__(self) -> None:
        if not isinstance(self.subject, EntityRef):
            raise WorldError("invalid_claim")
        _text(self.predicate, "invalid_claim")
        _cognition(self)


@dataclass(frozen=True, slots=True)
class WorldAnswer:
    world_id: str
    revision: int
    status: WorldStatus
    value: JsonValue | EntityRef | None
    candidates: tuple[WorldCandidate, ...]
    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    valid_at: ObservationTime | None

    def __post_init__(self) -> None:
        _identity(self.world_id, self.revision)
        _cognition(self)


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity: EntityRef
    matched: FrozenJsonObject
    supporting_claims: tuple[ClaimRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entity, EntityRef):
            raise WorldError("invalid_claim")
        object.__setattr__(self, "matched", freeze_json_object(self.matched))
        _tuple_field(self, "supporting_claims", ClaimRef)


@dataclass(frozen=True, slots=True)
class EntitySearchResult:
    world_id: str
    revision: int
    candidates: tuple[EntityCandidate, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _identity(self.world_id, self.revision)
        _tuple_field(self, "candidates", EntityCandidate)
        if type(self.truncated) is not bool:
            raise WorldError("invalid_claim")


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    world_id: str
    revision: int
    captured_at: datetime
    entries: tuple[WorldEntry, ...]

    def __post_init__(self) -> None:
        _identity(self.world_id, self.revision)
        _time(self.captured_at, "invalid_claim")
        _tuple_field(self, "entries", WorldEntry)


@dataclass(frozen=True, slots=True)
class WorldReceipt:
    world_id: str
    revision: int
    changed: bool
    observation_ref: ObservationRef | None
    committed_claim_refs: tuple[ClaimRef, ...]

    def __post_init__(self) -> None:
        _identity(self.world_id, self.revision)
        _tuple_field(self, "committed_claim_refs", ClaimRef)
        if type(self.changed) is not bool or (
            self.observation_ref is not None
            and not isinstance(self.observation_ref, ObservationRef)
        ):
            raise WorldError("invalid_observation")


@dataclass(frozen=True, slots=True)
class WorldLimits:
    max_entities: int = 1024
    max_observations: int = 4096
    max_claims: int = 8192
    max_evidence_metadata: int = 8192
    max_payload_bytes: int = 1_048_576
    max_media_refs: int = 1024
    max_search_results: int = 64

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise ValueError("World limits must be positive integers.")


def _identity(world_id: str, revision: int) -> None:
    _text(world_id, "invalid_claim")
    if type(revision) is not int or revision < 0:
        raise WorldError("invalid_claim")


def _tuple_field(owner: object, name: str, item_type: type) -> None:
    values = tuple(getattr(owner, name))
    if not all(isinstance(item, item_type) for item in values):
        raise WorldError("invalid_claim")
    object.__setattr__(owner, name, values)


def _cognition(owner: WorldCandidate | WorldEntry | WorldAnswer) -> None:
    if not isinstance(owner.value, EntityRef):
        object.__setattr__(owner, "value", freeze_json(owner.value))
    _tuple_field(owner, "supporting_claims", ClaimRef)
    _tuple_field(owner, "evidence_refs", EvidenceRef)
    if owner.valid_at is not None and not isinstance(owner.valid_at, ObservationTime):
        raise WorldError("invalid_claim")
    if isinstance(owner, (WorldEntry, WorldAnswer)):
        _tuple_field(owner, "candidates", WorldCandidate)
        if not isinstance(owner.status, WorldStatus):
            raise WorldError("invalid_claim")
        if (
            owner.status in (WorldStatus.UNKNOWN, WorldStatus.CONFLICTED)
            and owner.value is not None
        ):
            raise WorldError("invalid_claim")
