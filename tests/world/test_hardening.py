import asyncio
from dataclasses import replace
from datetime import datetime

import pytest

from roboagent.message import FrozenJsonObject
from roboagent.world import (
    ClaimKind,
    ClaimRef,
    EntityRef,
    EvidenceRef,
    ObservationTime,
    World,
    WorldCandidate,
    WorldEntry,
    WorldError,
    WorldLimits,
    WorldSnapshot,
    WorldStatus,
)
from tests.world.test_world import NOW, claim, observation, world


def test_concurrent_transactions_snapshot_consistency_and_capacity_backpressure():
    async def check():
        w = world(limits=WorldLimits(max_observations=32, max_claims=32))

        async def writer(index):
            try:
                c = replace(
                    claim(str(index), obs=str(index)), subject=EntityRef(str(index))
                )
                await w.observe(observation(str(index)), claims=(c,))
                return True
            except WorldError as exc:
                assert exc.code == "world_capacity_exceeded"
                return False

        outcomes = await asyncio.gather(*(writer(index) for index in range(64)))
        assert sum(outcomes) == 32
        snapshot = await w.snapshot()
        assert snapshot.revision == len(snapshot.entries) == 32
        assert all(entry.status is WorldStatus.KNOWN for entry in snapshot.entries)
        assert not hasattr(snapshot, "truncated")

    asyncio.run(check())


def test_evidence_dag_hypothesis_correction_identity_and_immutable_json():
    async def check():
        w = world()
        mutable = {"nested": [1, 2]}
        await w.observe(observation(), claims=[claim(value=mutable)])
        mutable["nested"].append(3)
        assert (await w.query(EntityRef("cube"), "color")).value == {"nested": [1, 2]}
        hypothesis = replace(claim("h"), kind=ClaimKind.HYPOTHESIS, evidence_refs=())
        inference = replace(
            claim("i"),
            subject=EntityRef("unproven"),
            kind=ClaimKind.INFERENCE,
            evidence_refs=(EvidenceRef("claim", "adapter", "h"),),
        )
        await w.add_claims([inference, hypothesis])  # forward DAG references supported
        assert (
            await w.query(EntityRef("unproven"), "color")
        ).status is WorldStatus.UNKNOWN
        corrected = replace(
            claim("correct"),
            subject=EntityRef("actual_cube"),
            supersedes=(ClaimRef("c1"), ClaimRef("h")),
        )
        await w.add_claims([corrected])
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.UNKNOWN
        assert (await w.query(EntityRef("actual_cube"), "color")).value == "red"

    asyncio.run(check())


def test_old_different_source_results_do_not_replace_newer_sample_time():
    async def check():
        w = world()
        await w.observe(observation(), claims=[claim(time=20)])
        await w.add_claims([claim("cloud_old", "blue", source="other", time=10)])
        assert (await w.query(EntityRef("cube"), "color")).value == "red"
        await w.add_claims([claim("same", "blue", source="other", time=20)])
        assert (
            await w.query(EntityRef("cube"), "color")
        ).status is WorldStatus.CONFLICTED

    asyncio.run(check())


def test_output_contracts_deep_freeze_sequences_and_validate_semantics():
    mutable = {"x": [1]}
    candidate = WorldCandidate(mutable, [ClaimRef("c")], [], None)
    mutable["x"].append(2)
    assert candidate.value == {"x": [1]}
    assert candidate.supporting_claims == (ClaimRef("c"),)
    entry = WorldEntry(
        EntityRef("e"), "p", WorldStatus.CONFLICTED, None, [candidate], [], [], None
    )
    entries = [entry]
    snapshot = WorldSnapshot("w", 1, NOW, entries)
    entries.clear()
    assert snapshot.entries == (entry,)
    with pytest.raises(WorldError, match="invalid_claim"):
        replace(entry, value="incorrect")
    with pytest.raises(WorldError):
        WorldSnapshot("w", 1, datetime.now(), ())


def test_invalid_clock_resolver_is_explicit_and_does_not_commit():
    class BadClock:
        def now(self, clock_id):
            return ObservationTime("different_epoch", 100)

    async def check():
        w = world(clock_resolver=BadClock())
        await w.observe(observation(), claims=[claim(valid_for_ns=1)])
        with pytest.raises(WorldError, match="clock_unavailable"):
            await w.snapshot()

    asyncio.run(check())


def test_world_loop_ownership_and_host_configuration():
    w = World("deny-by-default")

    async def check():
        with pytest.raises(WorldError, match="source_not_allowed"):
            await w.observe(observation())
        with pytest.raises(WorldError, match="invalid_observation"):
            await w.observe(None)
        with pytest.raises(WorldError, match="invalid_claim"):
            await w.add_claims(None)
        assert (await w.snapshot()).revision == 0

    asyncio.run(check())
    with pytest.raises(RuntimeError, match="owning event loop"):
        asyncio.run(w.snapshot())


def test_json_match_is_order_independent_and_type_precise():
    async def check():
        w = world()
        await w.observe(observation(), claims=[claim(value={"b": 2, "a": True})])
        assert (
            await w.find_entities({"color": FrozenJsonObject({"a": True, "b": 2})})
        ).candidates
        assert not (
            await w.find_entities({"color": FrozenJsonObject({"a": 1, "b": 2})})
        ).candidates

    asyncio.run(check())


def test_corrected_evidence_cannot_support_current_inference():
    async def check():
        w = world()
        await w.observe(observation(), claims=[claim()])
        derived = replace(
            claim("derived"),
            subject=EntityRef("inferred"),
            kind=ClaimKind.INFERENCE,
            evidence_refs=(EvidenceRef("claim", "adapter", "c1"),),
        )
        await w.add_claims([derived])
        assert (
            await w.query(EntityRef("inferred"), "color")
        ).status is WorldStatus.KNOWN
        await w.add_claims([claim("correction", "blue", supersedes=(ClaimRef("c1"),))])
        assert (
            await w.query(EntityRef("inferred"), "color")
        ).status is WorldStatus.UNKNOWN

    asyncio.run(check())


def test_transaction_json_identity_distinguishes_boolean_from_integer():
    async def check():
        w = world()
        await w.observe(
            observation(payload={"b": 2, "a": True}), claims=[claim(value=True)]
        )
        assert not (
            await w.observe(
                observation(payload={"a": True, "b": 2}), claims=[claim(value=True)]
            )
        ).changed
        with pytest.raises(WorldError, match="observation_conflict"):
            await w.observe(
                observation(payload={"a": 1, "b": 2}), claims=[claim(value=True)]
            )
        with pytest.raises(WorldError, match="claim_conflict"):
            await w.add_claims([claim(value=1)])
        assert (await w.snapshot()).revision == 1

    asyncio.run(check())


def test_unrelated_scope_does_not_mask_live_conflict():
    async def check():
        w = world()
        await w.observe(
            observation(),
            claims=[
                claim("a1", "red", scope="a"),
                claim("a2", "blue", scope="a", source="other"),
            ],
        )
        before = await w.query(EntityRef("cube"), "color")
        assert before.status is WorldStatus.CONFLICTED
        await w.add_claims([claim("b", "green", scope="b")])
        after = await w.query(EntityRef("cube"), "color")
        assert after.status is WorldStatus.CONFLICTED
        assert after.value is None
        assert {candidate.value for candidate in after.candidates} == {
            "red",
            "blue",
            "green",
        }

    asyncio.run(check())


def test_relation_values_keep_entity_identity_distinct_from_json_objects():
    async def check():
        from roboagent.world import create_world_tools
        from roboagent.tool import ToolContext
        from roboagent.runtime import RuntimeCancellation

        w = world()
        await w.observe(observation(), claims=[claim(value=EntityRef("table"))])
        query = create_world_tools(w)[0]
        result = await query.execute(
            FrozenJsonObject({"entity_id": "cube", "predicate": "color"}),
            ToolContext("run", "session", RuntimeCancellation()),
        )
        assert result.value["value"] == {"entity_ref": {"id": "table"}}
        assert result.value["value_kind"] == "entity_ref"
        with pytest.raises(WorldError, match="claim_conflict"):
            await w.add_claims([claim(value={"entity_ref": {"id": "table"}})])
        await w.add_claims([claim("literal", {"id": "table"}, time=20)])
        result = await query.execute(
            FrozenJsonObject({"entity_id": "cube", "predicate": "color"}),
            ToolContext("run", "session", RuntimeCancellation()),
        )
        assert result.value["value"] == {"id": "table"}
        assert result.value["value_kind"] == "json"

    asyncio.run(check())


def test_future_valid_at_is_not_current_even_without_automatic_ttl():
    async def check():
        from tests.world.test_world import Clock

        clock = Clock()
        clock.times["sim:1"] = 5
        w = world(clock_resolver=clock)
        await w.observe(observation(), claims=[claim(time=10)])
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.UNKNOWN
        clock.times["sim:1"] = 10
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.KNOWN
        assert (await w.snapshot()).revision == 1

    asyncio.run(check())


def test_world_ids_isolate_same_entity_and_evidence_names():
    async def check():
        first = World("first", allowed_sources=("sensor", "adapter"))
        second = World("second", allowed_sources=("sensor", "adapter"))
        await first.observe(observation(), claims=[claim(value="red")])
        with pytest.raises(WorldError, match="evidence_not_found"):
            await second.add_claims([claim(value="blue")])
        assert (await second.snapshot()).revision == 0
        await second.observe(observation(), claims=[claim(value="blue")])
        assert (await first.query(EntityRef("cube"), "color")).value == "red"
        assert (await second.query(EntityRef("cube"), "color")).value == "blue"

    asyncio.run(check())
