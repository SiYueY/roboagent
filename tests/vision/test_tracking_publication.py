import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from roboagent.interaction.vision import (
    AssociationStatus,
    EstimateStatus,
    PoseResult,
    PositionEstimate,
    PositionResult,
    PrincipalAxesEstimate,
    OrientationResult,
    PublicationStatus,
    SemanticAnalysis,
    SpatialResults,
    TrackingStatus,
    TrackingPolicy,
    VisionError,
    VisionLimits,
    VisionWorldAdapter,
    VisionWorldBinding,
    VisionWorldPolicy,
    VisualRelation,
    VisualTracker,
)
from roboagent.world import World, WorldLimits, ObservationTime, WorldStatus
from tests.vision.support import capture, detection, scene, RECEIVED


def analysis(sample, objects=None, relations=(), id="analysis"):
    return SemanticAnalysis(id, scene(sample, objects, relations))


def tracker(**kwargs):
    t = VisualTracker(namespace="instance", stream_id="camera", **kwargs)
    t.activate_epoch("clock")
    return t


def test_retry_reanalysis_and_epoch_never_rewind():
    t = tracker()
    first = capture()
    a = analysis(first)
    original = t.update(first, a)
    assert t.update(first, a).status is TrackingStatus.CACHED
    assert (
        t.update(first, replace(a, analysis_id="new")).associations
        == original.associations
    )
    changed = analysis(first, [detection("new-id")], id="other")
    assert t.update(first, changed).reason == "tracking_analysis_conflict"
    next_capture = capture(sequence=2)
    moved = t.update(next_capture, analysis(next_capture))
    assert moved.associations[0].track_id == original.associations[0].track_id
    late = capture(sequence=1)
    assert t.update(late, analysis(late)).reason == "tracking_out_of_order"
    t.activate_epoch("new-clock")
    new = capture(clock="new-clock")
    restarted = t.update(new, analysis(new))
    assert restarted.associations[0].track_id != original.associations[0].track_id
    assert t.update(first, a).reason == "tracking_epoch_inactive"


def test_one_to_one_competition_is_ambiguous_not_duplicate_identity():
    t = tracker()
    first = capture()
    t.update(first, analysis(first))
    sample = capture(sequence=1)
    result = t.update(sample, analysis(sample, [detection("a"), detection("b")]))
    assert all(
        a.status is AssociationStatus.AMBIGUOUS and a.track_id is None
        for a in result.associations
    )


def test_bounded_cache_and_failed_capacity_do_not_advance():
    t = tracker(limits=VisionLimits(max_tracking_cache_frames=1, max_tracks=1))
    first = capture()
    t.update(first, analysis(first))
    second = capture(sequence=1)
    t.update(second, analysis(second))
    assert t.update(first, analysis(first)).reason == "tracking_out_of_order"
    third = capture(sequence=2)
    rejected = t.update(third, analysis(third, [detection("blue", color="blue")]))
    assert rejected.reason == "tracking_capacity_exceeded"
    assert t.update(third, analysis(third)).status is TrackingStatus.UPDATED


class Clock:
    nanoseconds = 10_000_000

    def now(self, clock_id):
        return ObservationTime(clock_id, self.nanoseconds)


def adapter(limits=None, **kwargs):
    w = World(
        "w",
        clock_resolver=Clock(),
        allowed_sources=("visual", "semantic", "geometry"),
        allowed_scopes=("scope",),
        limits=limits,
    )
    return w, VisionWorldAdapter(
        VisionWorldBinding(w, "scope", "visual", "semantic", "geometry", **kwargs)
    )


def test_relation_aggregate_and_frozen_publication_retry():
    async def check():
        sample = capture()
        objects = [
            detection("a", x0=0.01, x1=0.25),
            detection("b", x0=0.3, x1=0.5),
            detection("c", x0=0.6, x1=0.9),
        ]
        a = analysis(
            sample,
            objects,
            [VisualRelation("a", "near", "b"), VisualRelation("a", "near", "c")],
        )
        tracks = tracker().update(sample, a)
        w, publisher = adapter()
        pub = publisher.prepare(sample, a, tracks, asserted_at=RECEIVED)
        first = await publisher.publish(pub)
        retry = await publisher.publish(pub)
        assert first.status is PublicationStatus.COMPLETE
        assert retry.claims_receipt.revision == first.claims_receipt.revision
        assert not retry.evidence_receipt.changed and not retry.claims_receipt.changed
        subject = next(
            c.subject
            for c in pub.claims
            if c.predicate == "vision.semantic.relation.near"
        )
        result = await w.query(subject, "vision.semantic.relation.near", scope="scope")
        assert result.status is WorldStatus.KNOWN and len(result.value["targets"]) == 2
        bad = replace(
            pub,
            claims=tuple(
                replace(c, asserted_at=c.asserted_at + timedelta(seconds=1))
                for c in pub.claims
            ),
        )
        assert (await publisher.publish(bad)).error_code == "claim_conflict"

    asyncio.run(check())


def test_partial_failure_keeps_evidence_and_has_no_partial_claims():
    async def check():
        sample = capture()
        a = analysis(sample)
        w, publisher = adapter(WorldLimits(max_claims=1))
        pub = publisher.prepare(sample, a, tracker().update(sample, a))
        result = await publisher.publish(pub)
        assert result.status is PublicationStatus.EVIDENCE_ONLY
        assert result.error_code == "world_capacity_exceeded"
        assert result.evidence_receipt.revision == 1
        assert (await w.snapshot()).entries == ()

    asyncio.run(check())


def test_ambiguous_pose_and_unresolved_relations_never_publish():
    async def check():
        sample = capture()
        a = analysis(sample)
        tracks = tracker().update(sample, a)
        w, publisher = adapter()
        spatial = SpatialResults(
            sample.ref,
            poses=(
                PoseResult(sample.ref, "a", EstimateStatus.AMBIGUOUS, None, "symmetry"),
            ),
        )
        pub = publisher.prepare(sample, a, tracks, spatial, asserted_at=RECEIVED)
        assert not any(c.predicate == "vision.geometry.pose" for c in pub.claims)
        await publisher.publish(pub)
        empty = capture(sequence=1)
        absent = analysis(empty, [])
        pub2 = publisher.prepare(
            empty, absent, tracker().update(empty, absent), asserted_at=RECEIVED
        )
        assert not pub2.claims
        await publisher.publish(pub2)
        assert not any(c.predicate == "visible" for c in pub2.claims)

    asyncio.run(check())


def test_mapping_collision_is_rejected_before_write():
    sample = capture()
    a = analysis(sample)
    w, publisher = adapter(
        predicate_mapping={"vision.semantic.color": "vision.semantic.category"}
    )
    with pytest.raises(VisionError, match="vision_predicate_mapping_conflict"):
        publisher.prepare(sample, a, tracker().update(sample, a))


@pytest.mark.parametrize(
    "current_frame,offset,stable,matched",
    [
        ("world", 0.02, True, True),
        ("world", 0.3, True, False),
        ("optical", 0.02, True, False),
        ("world", 0.02, False, False),
    ],
)
def test_stable_3d_gate_survives_camera_motion(current_frame, offset, stable, matched):
    t = tracker(policy=TrackingPolicy(stable_frames={"world"} if stable else set()))
    first, second = capture(), capture(sequence=1)

    def positions(sample, frame, x):
        return SpatialResults(
            sample.ref,
            positions=(
                PositionResult(
                    sample.ref,
                    "a",
                    EstimateStatus.AVAILABLE,
                    PositionEstimate(sample.ref, "a", (x, 0, 1), frame),
                ),
            ),
        )

    original = t.update(
        first,
        analysis(first, [detection(x0=0.05, x1=0.15)]),
        positions(first, "world", 0),
    )
    result = t.update(
        second,
        analysis(second, [detection(x0=0.75, x1=0.85)]),
        positions(second, current_frame, offset),
    )
    assert (result.associations[0].status is AssociationStatus.MATCHED) == matched
    assert (
        result.associations[0].track_id == original.associations[0].track_id
    ) == matched


@pytest.mark.parametrize(
    "ttl,expected", [(1_000_000_000, WorldStatus.STALE), (None, WorldStatus.KNOWN)]
)
def test_semantic_freshness_and_explicit_permanent_policy(ttl, expected):
    async def check():
        clock = Clock()
        w = World(
            "freshness",
            clock_resolver=clock,
            allowed_sources=("visual", "semantic", "geometry"),
        )
        policy = (
            VisionWorldPolicy()
            if ttl is not None
            else VisionWorldPolicy(semantic_ttl_ns=None)
        )
        publisher = VisionWorldAdapter(
            VisionWorldBinding(
                w,
                None,
                "visual",
                "semantic",
                "geometry",
                policy=policy,
            )
        )
        sample = capture()
        a = analysis(
            sample,
            [
                replace(
                    detection("a", x0=0.1, x1=0.4),
                    attributes={"state": "open"},
                    description="open box",
                ),
                detection("b", x0=0.6, x1=0.9),
            ],
            [VisualRelation("a", "near", "b")],
        )
        t = tracker()
        pub = publisher.prepare(sample, a, t.update(sample, a), asserted_at=RECEIVED)
        await publisher.publish(pub)
        semantics = [
            c
            for c in pub.claims
            if c.predicate.startswith("vision.semantic.")
            and c.predicate != "vision.semantic.region"
        ]
        assert {c.predicate for c in semantics} >= {
            "vision.semantic.category",
            "vision.semantic.description",
            "vision.semantic.state",
            "vision.semantic.relation.near",
        }
        for c in semantics:
            assert c.valid_for_ns == ttl
            assert (await w.query(c.subject, c.predicate)).status is WorldStatus.KNOWN
        # An omission adds no negative claims and does not refresh old semantics.
        empty = capture(sequence=1)
        absent = analysis(empty, [])
        await publisher.publish(
            publisher.prepare(empty, absent, t.update(empty, absent))
        )
        clock.nanoseconds = 1_000_000_001
        for c in semantics:
            assert (await w.query(c.subject, c.predicate)).status is expected

    asyncio.run(check())


def test_principal_axes_publication_keeps_pca_semantics():
    sample = capture()
    a = analysis(sample)
    spatial = SpatialResults(
        sample.ref,
        orientations=(
            OrientationResult(
                sample.ref,
                "a",
                EstimateStatus.AVAILABLE,
                PrincipalAxesEstimate(
                    sample.ref,
                    "a",
                    "optical",
                    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    (3, 2, 1),
                ),
            ),
        ),
    )
    _, publisher = adapter()
    pub = publisher.prepare(sample, a, tracker().update(sample, a, spatial), spatial)
    geometry = [c for c in pub.claims if c.predicate.startswith("vision.geometry.")]
    assert [c.predicate for c in geometry] == ["vision.geometry.principal_axes"]
    assert geometry[0].value["frame_id"] == "optical"
    assert "directions" in geometry[0].value
