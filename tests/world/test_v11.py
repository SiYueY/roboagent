import asyncio
from dataclasses import replace

import pytest

from roboagent.world import EntityMatchStatus, EntityRef, WorldEntryKey, WorldStatus
from tests.world.test_world import Clock, claim, observation, world


@pytest.mark.parametrize(
    "claims,status,values",
    [
        ([claim("r", "red", valid_for_ns=20)], WorldStatus.KNOWN, {"red"}),
        (
            [claim("r", "red"), claim("b", "blue", source="other")],
            WorldStatus.CONFLICTED,
            {"red", "blue"},
        ),
        (
            [claim("r", "red"), claim("b", "blue", clock="missing", valid_for_ns=20)],
            WorldStatus.UNKNOWN,
            {"red", "blue"},
        ),
        (
            [
                claim("r", "red", time=0, valid_for_ns=1),
                claim("b", "blue", clock="missing", valid_for_ns=20),
            ],
            WorldStatus.UNKNOWN,
            {"red", "blue"},
        ),
        (
            [
                claim("r", "red"),
                claim("b", "blue", source="other"),
                claim("g", "green", clock="missing", valid_for_ns=20),
            ],
            WorldStatus.CONFLICTED,
            {"red", "blue", "green"},
        ),
        (
            [claim("old", "red", time=0), claim("new", "blue", time=5)],
            WorldStatus.KNOWN,
            {"blue"},
        ),
        (
            [
                claim("old", "red", time=0, valid_for_ns=100),
                claim("new", "blue", time=5, valid_for_ns=1),
            ],
            WorldStatus.STALE,
            {"blue"},
        ),
    ],
)
def test_resolution_matrix(claims, status, values):
    async def check():
        w = world(clock_resolver=Clock())
        await w.observe(observation(), claims=claims)
        entry = (await w.snapshot()).lookup(WorldEntryKey(EntityRef("cube"), "color"))
        assert entry.status is status
        assert {candidate.value for candidate in entry.candidates} == values
        assert (
            entry.value in values
            if status is WorldStatus.KNOWN
            else entry.value is None
        )
        assert set(entry.supporting_claims) == {
            ref for candidate in entry.candidates for ref in candidate.supporting_claims
        }
        assert set(entry.evidence_refs) == {
            ref for candidate in entry.candidates for ref in candidate.evidence_refs
        }

    asyncio.run(check())


def test_exact_scope_possible_matching_and_typed_relations():
    async def check():
        w = world(clock_resolver=Clock())
        await w.observe(
            observation(),
            claims=[
                claim("red", "red", scope="a"),
                claim("old", "red", time=0, valid_for_ns=1),
                replace(claim("relation", EntityRef("table")), predicate="inside"),
            ],
        )
        assert (await w.query(EntityRef("cube"), "color", scope="a")).value == "red"
        assert (await w.query(EntityRef("cube"), "color")).status is WorldStatus.STALE
        found = await w.find_entities({"color": "red"}, include_possible=True)
        assert found.candidates[0].status is EntityMatchStatus.POSSIBLE
        assert found.candidates[0].uncertain == ("color",)
        assert (await w.find_entities({"inside": EntityRef("table")})).candidates
        assert not (
            await w.find_entities({"inside": {"entity_ref": {"id": "table"}}})
        ).candidates
        snapshot = await w.snapshot()
        assert snapshot.lookup(WorldEntryKey(EntityRef("cube"), "absent")) is None

    asyncio.run(check())


def test_unavailable_clock_does_not_prove_a_claim_is_live_without_ttl():
    async def check():
        w = world(clock_resolver=None)
        await w.observe(observation(), claims=[claim()])
        answer = await w.query(EntityRef("cube"), "color")
        assert answer.status is WorldStatus.UNKNOWN
        assert {c.value for c in answer.candidates} == {"red"}

    asyncio.run(check())
