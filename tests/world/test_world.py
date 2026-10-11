import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from roboagent.world import (
    Claim,
    ClaimKind,
    ClaimRef,
    EntityRef,
    EvidenceRef,
    Observation,
    ObservationRef,
    ObservationTime,
    ResourceRef,
    World,
    WorldError,
    WorldLimits,
    WorldStatus,
)

NOW = datetime.now(timezone.utc)


class Clock:
    def __init__(self):
        self.times = {"sim:1": 10}

    def now(self, clock_id):
        value = self.times.get(clock_id)
        return None if value is None else ObservationTime(clock_id, value)


def observation(id="1", *, clock="sim:1", time=10, payload=None, scope=None):
    return Observation(
        ObservationRef("sensor", id),
        "state",
        ObservationTime(clock, time),
        NOW,
        payload,
        scope,
    )


def claim(
    id="c1",
    value="red",
    *,
    obs="1",
    time=10,
    source="adapter",
    scope=None,
    clock="sim:1",
    **kwargs,
):
    return Claim(
        ClaimRef(id),
        EntityRef("cube"),
        "color",
        value,
        ClaimKind.MEASUREMENT,
        source,
        scope,
        (EvidenceRef("observation", "sensor", obs),),
        NOW,
        ObservationTime(clock, time),
        **kwargs,
    )


def world(**kwargs):
    return World(
        "test",
        allowed_sources=("sensor", "adapter", "other"),
        allowed_scopes=(None, "a", "b"),
        **kwargs,
    )


def test_transaction_identity_atomicity_and_limits():
    async def check():
        w = world()
        o, c = observation(), claim()
        first = await w.observe(o, claims=[c])
        assert first.changed and first.revision == 1
        assert not (await w.observe(o, claims=[c])).changed
        assert not (await w.add_claims([c])).changed
        for changed in (replace(o, payload="different"),):
            with pytest.raises(WorldError, match="observation_conflict"):
                await w.observe(changed, claims=[c])
        with pytest.raises(WorldError, match="observation_conflict"):
            await w.observe(o)
        with pytest.raises(WorldError, match="claim_conflict"):
            await w.add_claims([replace(c, value="blue")])
        with pytest.raises(WorldError, match="evidence_not_found"):
            await w.add_claims([claim("ok"), claim("bad", obs="missing")])
        assert (await w.snapshot()).revision == 1
        small = world(limits=WorldLimits(max_claims=1))
        with pytest.raises(WorldError, match="world_capacity_exceeded"):
            await small.observe(o, claims=[c, claim("c2")])
        assert (await small.snapshot()).revision == 0

    asyncio.run(check())


def test_clock_freshness_snapshot_and_pause():
    async def check():
        clock = Clock()
        w = world(clock_resolver=clock)
        await w.observe(observation(), claims=[claim(valid_for_ns=10)])
        frozen = await w.snapshot()
        assert frozen.entries[0].status is WorldStatus.KNOWN
        assert (await w.snapshot()).entries == frozen.entries  # paused sim
        clock.times["sim:1"] = 20
        stale = await w.snapshot()
        assert stale.revision == frozen.revision
        assert stale.entries[0].status is WorldStatus.STALE
        assert frozen.entries[0].status is WorldStatus.KNOWN
        del clock.times["sim:1"]
        assert (await w.snapshot()).entries[0].status is WorldStatus.UNKNOWN
        clock.times["sim:2"] = 100000
        assert (await w.snapshot()).entries[0].status is WorldStatus.UNKNOWN

    asyncio.run(check())


def test_late_claim_conflict_scope_correction_search():
    async def check():
        w = world()
        await w.observe(observation(), claims=[claim(time=20)])
        await w.add_claims([claim("late", "orange", time=10)])
        assert (await w.query(EntityRef("cube"), "color")).value == "red"
        await w.add_claims([claim("other", "blue", source="other", clock="device:1")])
        result = await w.query(EntityRef("cube"), "color")
        assert result.status is WorldStatus.CONFLICTED and len(result.candidates) == 2
        await w.add_claims(
            [
                claim(
                    "fixed",
                    "red",
                    source="other",
                    clock="device:1",
                    supersedes=(ClaimRef("other"),),
                )
            ]
        )
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.KNOWN
        await w.add_claims([replace(claim("cube2"), subject=EntityRef("cube2"))])
        search = await w.find_entities({"color": "red"}, limit=1)
        assert search.truncated and search.candidates[0].entity.id == "cube"
        await w.add_claims(
            [claim("scope1", "orange", scope="a"), claim("scope2", "green", scope="b")]
        )
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.UNKNOWN
        assert (
            await w.query(EntityRef("missing"), "color")
        ).status is WorldStatus.UNKNOWN

    asyncio.run(check())


def test_hypothesis_authorization_graph_and_resource_metadata():
    async def check():
        w = world()
        await w.add_claims(
            [replace(claim(), kind=ClaimKind.HYPOTHESIS, evidence_refs=())]
        )
        assert (await w.snapshot()).entries[0].status is WorldStatus.UNKNOWN
        with pytest.raises(WorldError, match="source_not_allowed"):
            await w.observe(replace(observation(), ref=ObservationRef("forged", "1")))
        with pytest.raises(WorldError, match="scope_not_allowed"):
            await w.observe(replace(observation(), scope="forbidden"))
        with pytest.raises(WorldError, match="invalid_claim"):
            await w.add_claims(
                [
                    replace(
                        claim("a"), evidence_refs=(EvidenceRef("claim", None, "b"),)
                    ),
                    replace(
                        claim("b"), evidence_refs=(EvidenceRef("claim", None, "a"),)
                    ),
                ]
            )
        o = observation(
            payload=ResourceRef(
                "opaque://expired", "image/jpeg", 100, "digest", available=False
            )
        )
        await w.observe(o)
        # World keeps provenance and never fetches expired media.
        await w.add_claims([claim("supported")])
        assert (await w.snapshot()).revision == 3

    asyncio.run(check())


@pytest.mark.parametrize("limits", [{"max_claims": 0}, {"max_entities": True}])
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        WorldLimits(**limits)
