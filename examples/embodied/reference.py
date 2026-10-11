"""Deterministic contract fixture; cloud grounding has separate acceptance tests."""

from __future__ import annotations

import json

from roboagent import Agent
from roboagent.agent import RunConfig
from roboagent.context import ContextDataSegment, MessageSegment
from roboagent.message import (
    AssistantMessage,
    JsonContent,
    ToolResultMessage,
    ToolCall,
    UserMessage,
    freeze_json_object,
    canonical_json_dumps,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    ToolCallStarted,
    ToolCallCompleted,
)
from roboagent.tool import ToolRegistry
from roboagent.world import create_world_tools
from .contracts import PhysicalPolicy
from .tabletop import Tabletop


def _evidence(entries, target, roles):
    def lookup(subject, predicate):
        entry = next(
            e
            for e in entries
            if e["subject"]["id"] == subject
            and e["predicate"] == predicate
            and e["scope"] == "workspace"
        )
        if entry["status"] != "known":
            raise ValueError("Evidence not ready.")
        return entry

    selection = [lookup(target, p) for p in ("category", "color")]
    return {
        "selection": {
            "scope": "workspace",
            "filters": [
                {
                    "predicate": e["predicate"],
                    "value_kind": e["value_kind"],
                    "value": e["value"],
                }
                for e in selection
            ],
            "claim_refs": sorted(
                {ref["id"] for e in selection for ref in e["supporting_claims"]}
            ),
        },
        "readiness": [
            {
                "role": role,
                "subject": subject,
                "predicate": predicate,
                "scope": "workspace",
                "claim_refs": [
                    r["id"] for r in lookup(subject, predicate)["supporting_claims"]
                ],
            }
            for role, subject, predicate in roles
        ],
    }


def _call(identifier, name, arguments=None):
    return ToolCall(identifier, name, freeze_json_object(arguments or {}))


class TabletopModel:
    capabilities = ModelCapabilities(tool_calling=True)

    def __init__(self):
        self.turn = 0
        self.target: str | None = None

    async def stream(self, context, settings=None):
        self.turn += 1
        entries = json.loads(
            next(s.text for s in context.segments if isinstance(s, ContextDataSegment))
        )
        if self.turn == 2:
            search = next(
                segment.message
                for segment in context.segments
                if isinstance(segment, MessageSegment)
                and isinstance(segment.message, ToolResultMessage)
                and segment.message.tool_name == "world_find_entities"
            )
            payload = json.loads(
                canonical_json_dumps(
                    next(
                        part.value
                        for part in search.content
                        if isinstance(part, JsonContent)
                    )
                )
            )
            if not isinstance(payload, dict):
                raise ValueError("Invalid search result.")
            candidates = payload["candidates"]
            if (
                payload["truncated"]
                or len(candidates) != 1
                or candidates[0]["status"] != "confirmed"
            ):
                yield ResponseStarted("tabletop", 0)
                yield ResponseCompleted(
                    1,
                    ModelResponse(
                        AssistantMessage(
                            "Target requires observation or clarification."
                        ),
                        FinishReason.STOP,
                    ),
                )
                return
            self.target = str(candidates[0]["entity"]["id"])
        if self.turn in (3, 5):
            selection = {
                entry["predicate"]: entry
                for entry in entries
                if entry["subject"]["id"] == self.target
            }
            if any(
                selection[predicate]["status"] != "known"
                or selection[predicate]["value"] != value
                for predicate, value in (("category", "cube"), ("color", "red"))
            ):
                yield ResponseStarted("tabletop", 0)
                yield ResponseCompleted(
                    1,
                    ModelResponse(
                        AssistantMessage(
                            "Target selection changed; task remains unverified."
                        ),
                        FinishReason.STOP,
                    ),
                )
                return
        if self.turn == 1:
            call = _call(
                "search",
                "world_find_entities",
                {
                    "filters": [
                        {
                            "predicate": "category",
                            "value_kind": "json",
                            "value": "cube",
                        },
                        {"predicate": "color", "value_kind": "json", "value": "red"},
                    ],
                    "scope": "workspace",
                    "include_possible": True,
                },
            )
        elif self.turn == 2:
            call = _call("observe", "observe_scene")
        elif self.turn == 3:
            call = _call(
                "pick",
                "pick_object",
                {
                    "target_entity": self.target,
                    "decision_evidence": _evidence(
                        entries, self.target, [("target_pose", self.target, "pose")]
                    ),
                },
            )
        elif self.turn in (4, 6):
            call = _call(
                f"observe-{self.turn}", "observe_scene", {"verification": True}
            )
        elif self.turn == 5:
            call = _call(
                "place",
                "place_object",
                {
                    "target_entity": self.target,
                    "receptacle_entity": "basket",
                    "decision_evidence": _evidence(
                        entries,
                        self.target,
                        [
                            ("target_pose", self.target, "pose"),
                            ("receptacle_pose", "basket", "pose"),
                            ("holding", "robot", "holding"),
                        ],
                    ),
                },
            )
        else:

            def known(subject, predicate):
                return next(
                    e
                    for e in entries
                    if e["subject"]["id"] == subject and e["predicate"] == predicate
                )

            inside, holding, stable = (
                known(self.target, "inside"),
                known("robot", "holding"),
                known(self.target, "stable"),
            )
            verified = (
                all(e["status"] == "known" for e in (inside, holding, stable))
                and inside["value"] == {"entity_ref": {"id": "basket"}}
                and holding["value"] is None
                and stable["value"] is True
            )
            message = AssistantMessage(
                "Placement verified from post-action evidence."
                if verified
                else "Placement remains unverified."
            )
            yield ResponseStarted("tabletop", 0)
            yield ResponseCompleted(1, ModelResponse(message, FinishReason.STOP))
            return
        yield ResponseStarted("tabletop", 0)
        yield ToolCallStarted(1, 0, call.id, call.name)
        yield ToolCallCompleted(2, 0, call)
        yield ResponseCompleted(
            3,
            ModelResponse(AssistantMessage(tool_calls=(call,)), FinishReason.TOOL_CALL),
        )


async def main(model=None):
    table = Tabletop()
    await table.observe()
    from roboagent.context import PromptInput

    prompt = PromptInput("""Complete the user task using explicit World and physical Tools.
Use scope=workspace and include_possible=true for entity search. Observe when evidence is missing, stale or ambiguous.
Physical calls need decision_evidence.selection with scope, typed filters, and supporting claim_refs from current Context,
and decision_evidence.readiness with role, subject, predicate, scope, claim_refs.
Pick requires target_pose. Place requires target_pose, receptacle_pose and holding (subject robot, predicate holding).
Use category and color filters for target selection. Only use KNOWN source facts.
After every physical action call observe_scene with verification=true, then reason in a new turn.
Controller success is not task success. Complete only after inside=basket, robot.holding=null, and stable=true are KNOWN.
If a Tool fails, inspect or observe and make a new decision; never claim success without post-action evidence.""")
    agent = Agent(
        model=model or TabletopModel(),
        world=table.world,
        prompt=prompt,
        tool_registry=ToolRegistry((*create_world_tools(table.world), *table.tools())),
        tool_policy=PhysicalPolicy(frozenset(("pick_object", "place_object"))),
    )
    session = agent.new_session()
    try:
        result = await session.run(
            UserMessage("Put the red cube in the basket."),
            config=RunConfig(max_turns=16, timeout=180),
        )
    finally:
        await session.close()
    print(result.status.value, result.output)
    return result, table
