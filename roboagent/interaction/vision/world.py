"""Explicit Vision/World composition using immutable two-stage publications."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable, Mapping

from roboagent.message import canonical_json_digest, freeze_json, freeze_json_object
from roboagent.world import (
    Claim,
    ClaimKind,
    ClaimRef,
    EntityRef,
    EvidenceRef,
    Observation,
    ObservationRef,
    ObservationTime,
    World,
    WorldError,
    WorldReceipt,
)
from .image import capture_metadata, to_data
from .types import (
    CaptureRef,
    EstimateStatus,
    SemanticAnalysis,
    SpatialResults,
    PoseEstimate,
    VisionError,
)
from .tracking import TrackingStatus, association_input_digest


@dataclass(frozen=True, slots=True)
class VisionWorldPolicy:
    semantic_ttl_ns: int | None = None
    spatial_ttl_ns: int | None = 1_000_000_000
    visibility_ttl_ns: int | None = 1_000_000_000

    def __post_init__(self):
        from .types import integer

        for value in (
            self.semantic_ttl_ns,
            self.spatial_ttl_ns,
            self.visibility_ttl_ns,
        ):
            if value is not None:
                integer(value)


@dataclass(frozen=True, slots=True)
class VisionWorldBinding:
    world: World
    scope: str | None
    observation_source: str
    semantic_claim_source: str
    geometry_claim_source: str
    track_to_entity: Callable | None = None
    policy: VisionWorldPolicy = field(default_factory=VisionWorldPolicy)
    predicate_mapping: Mapping[str, str] = field(default_factory=dict)
    value_encoder: Callable | None = None

    def __post_init__(self):
        from .types import text_id

        if not isinstance(self.world, World):
            raise VisionError("world_binding_invalid")
        for key, value in self.predicate_mapping.items():
            text_id(key)
            text_id(value)
        for source in (
            self.observation_source,
            self.semantic_claim_source,
            self.geometry_claim_source,
        ):
            text_id(source)
        if self.scope is not None:
            text_id(self.scope)
        object.__setattr__(
            self, "predicate_mapping", freeze_json_object(self.predicate_mapping)
        )


@dataclass(frozen=True, slots=True)
class WorldPublication:
    capture_ref: CaptureRef
    analysis_id: str
    observation: Observation
    claims: tuple[Claim, ...]
    world_id: str

    def __post_init__(self):
        object.__setattr__(self, "claims", tuple(self.claims))


class PublicationStatus(str, Enum):
    NOT_COMMITTED = "not_committed"
    EVIDENCE_ONLY = "evidence_only"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: PublicationStatus
    evidence_receipt: WorldReceipt | None
    claims_receipt: WorldReceipt | None
    error_code: str | None


class VisionWorldAdapter:
    def __init__(self, binding: VisionWorldBinding):
        self.binding = binding

    def _entity(self, tid, stream):
        if self.binding.track_to_entity is not None:
            entity = self.binding.track_to_entity(tid)
            if not isinstance(entity, EntityRef):
                raise VisionError("entity_mapping_invalid")
            return entity
        return EntityRef(
            "vision:"
            + canonical_json_digest([stream, tid.namespace, tid.clock_id, tid.value])
        )

    def prepare(
        self,
        capture,
        analysis: SemanticAnalysis,
        tracking,
        spatial: SpatialResults | None = None,
        *,
        asserted_at: datetime | None = None,
    ) -> WorldPublication:
        b = self.binding
        ref = capture.ref
        if (
            analysis.scene.ref != ref
            or tracking.capture_ref != ref
            or spatial is not None
            and spatial.capture_ref != ref
        ):
            raise VisionError("estimate_binding_invalid")
        if (
            tracking.status is not TrackingStatus.SKIPPED
            and tracking.input_digest
            != association_input_digest(capture, analysis.scene, spatial)
        ):
            raise VisionError("association_binding_invalid")
        scene = analysis.scene
        ids = {d.local_id for d in scene.objects}
        if (
            tracking.status is not TrackingStatus.SKIPPED
            and {a.detection_id for a in tracking.associations} != ids
        ):
            raise VisionError("association_binding_invalid")
        if spatial is not None and any(
            r.detection_id not in ids
            for results in (
                spatial.surface,
                spatial.positions,
                spatial.poses,
                spatial.orientations,
            )
            for r in results
        ):
            raise VisionError("estimate_binding_invalid")
        visual = capture.visual if hasattr(capture, "visual") else capture
        observation = Observation(
            ObservationRef(
                b.observation_source,
                "vision:" + canonical_json_digest([b.scope, to_data(ref)]),
            ),
            "visual_depth" if hasattr(capture, "depth") else "visual",
            ObservationTime(ref.clock_id, visual.captured_at.nanoseconds),
            visual.received_at,
            freeze_json(capture_metadata(capture)),
            b.scope,
        )
        evidence = (
            EvidenceRef("observation", b.observation_source, observation.ref.id),
        )
        asserted_at = asserted_at or datetime.now(timezone.utc)
        entities = {
            a.detection_id: self._entity(a.track_id, ref.stream_id)
            for a in tracking.associations
            if a.track_id is not None
        }
        if len(set(entities.values())) != len(entities):
            raise VisionError("entity_mapping_collision")
        values = {}

        def add(det, predicate, value, source, ttl):
            if det not in entities:
                return
            mapped = b.predicate_mapping.get(predicate, predicate)
            key = (entities[det], mapped)
            if key in values:
                raise VisionError("vision_predicate_mapping_conflict")
            encoded = b.value_encoder(predicate, value) if b.value_encoder else value
            values[key] = (source, freeze_json(encoded), ttl)

        for d in scene.objects:
            add(
                d.local_id,
                "vision.semantic.category",
                d.category,
                b.semantic_claim_source,
                b.policy.semantic_ttl_ns,
            )
            add(
                d.local_id,
                "vision.semantic.region",
                to_data(d.region),
                b.semantic_claim_source,
                b.policy.spatial_ttl_ns,
            )
            if d.description is not None:
                add(
                    d.local_id,
                    "vision.semantic.description",
                    d.description,
                    b.semantic_claim_source,
                    b.policy.semantic_ttl_ns,
                )
            for name, value in d.attributes.items():
                add(
                    d.local_id,
                    "vision.semantic." + name,
                    value,
                    b.semantic_claim_source,
                    b.policy.semantic_ttl_ns,
                )
            add(
                d.local_id,
                "vision.state.observed",
                True,
                b.semantic_claim_source,
                b.policy.visibility_ttl_ns,
            )
        relations: dict[tuple[str, str], set[str]] = {}
        for relation in scene.relations:
            if relation.subject_id in entities and relation.object_id in entities:
                relations.setdefault(
                    (relation.subject_id, relation.predicate), set()
                ).add(entities[relation.object_id].id)
        for (det, predicate), targets in sorted(relations.items()):
            add(
                det,
                "vision.semantic.relation." + predicate,
                {
                    "targets": sorted(targets),
                    "reference": "positive_visual_targets",
                    "complete": False,
                },
                b.semantic_claim_source,
                b.policy.semantic_ttl_ns,
            )
        if spatial:
            for group, name in (
                (spatial.surface, "surface_point"),
                (spatial.positions, "position"),
                (spatial.orientations, "orientation"),
                (spatial.poses, "pose"),
            ):
                for r in group:
                    if r.status is EstimateStatus.AVAILABLE:
                        if name == "pose" and not isinstance(r.estimate, PoseEstimate):
                            raise VisionError("pose_invalid")
                        add(
                            r.detection_id,
                            "vision.geometry." + name,
                            to_data(r.estimate),
                            b.geometry_claim_source,
                            b.policy.spatial_ttl_ns,
                        )
        claims = tuple(
            Claim(
                ClaimRef(
                    "vision:"
                    + canonical_json_digest(
                        [
                            source,
                            b.scope,
                            to_data(ref),
                            analysis.analysis_id,
                            entity.id,
                            predicate,
                        ]
                    )
                ),
                entity,
                predicate,
                value,
                ClaimKind.INTERPRETATION,
                source,
                b.scope,
                evidence,
                asserted_at,
                observation.observed_at,
                ttl,
            )
            for (entity, predicate), (source, value, ttl) in sorted(
                values.items(), key=lambda item: (item[0][0].id, item[0][1])
            )
        )
        return WorldPublication(
            ref, analysis.analysis_id, observation, claims, b.world.world_id
        )

    async def publish(
        self, publication: WorldPublication, *, cancellation=None, on_receipt=None
    ) -> PublicationResult:
        if publication.world_id != self.binding.world.world_id:
            raise VisionError("world_mismatch")
        b = self.binding
        if (
            publication.observation.ref.source != b.observation_source
            or publication.observation.scope != b.scope
        ):
            raise VisionError("publication_binding_invalid")
        if any(
            c.source not in (b.semantic_claim_source, b.geometry_claim_source)
            or c.scope != b.scope
            for c in publication.claims
        ):
            raise VisionError("publication_binding_invalid")
        evidence = None
        try:
            if cancellation:
                cancellation.raise_if_cancelled()
            evidence = await self.binding.world.observe(
                publication.observation, claims=()
            )
            if on_receipt is not None:
                on_receipt("evidence", evidence)
            if cancellation:
                cancellation.raise_if_cancelled()
            claims = await self.binding.world.add_claims(publication.claims)
            return PublicationResult(PublicationStatus.COMPLETE, evidence, claims, None)
        except WorldError as exc:
            return PublicationResult(
                PublicationStatus.EVIDENCE_ONLY
                if evidence
                else PublicationStatus.NOT_COMMITTED,
                evidence,
                None,
                exc.code,
            )
