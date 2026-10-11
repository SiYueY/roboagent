"""Explicit, read-only Agent capabilities over existing cognition."""

from roboagent.message import FrozenJsonObject, freeze_json
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolJsonContent,
    ToolErrorInfo,
    ToolExecutionFailure,
)
from ._codec import world_data
from .types import EntityRef, WorldError
from .world import World


def create_world_tools(world: World) -> tuple[Tool, ...]:
    if not isinstance(world, World):
        raise TypeError("world must be World.")

    async def query(arguments, context):
        context.cancellation.raise_if_cancelled()
        try:
            result = await world.query(
                EntityRef(arguments["entity_id"]), arguments["predicate"]
            )
        except WorldError as exc:
            raise ToolExecutionFailure(ToolErrorInfo(exc.code, exc.code)) from exc
        return ToolJsonContent(freeze_json(world_data(result)))

    async def find(arguments, context):
        context.cancellation.raise_if_cancelled()
        try:
            result = await world.find_entities(
                arguments["filters"],
                limit=arguments.get("limit", min(16, world.limits.max_search_results)),
            )
        except WorldError as exc:
            raise ToolExecutionFailure(ToolErrorInfo(exc.code, exc.code)) from exc
        return ToolJsonContent(freeze_json(world_data(result)))

    return (
        Tool(
            ToolDefinition(
                "world_query",
                "Query existing cognition without acquiring evidence or changing the environment.",
                FrozenJsonObject(
                    {
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "minLength": 1,
                                "x-world-entity-ref": True,
                            },
                            "predicate": {"type": "string", "minLength": 1},
                        },
                        "required": ["entity_id", "predicate"],
                        "additionalProperties": False,
                    }
                ),
            ),
            query,
            effect_kind=ToolEffectKind.READ_ONLY,
        ),
        Tool(
            ToolDefinition(
                "world_find_entities",
                "Find all matching candidates in existing cognition using structured filters. Does not observe the environment.",
                FrozenJsonObject(
                    {
                        "type": "object",
                        "properties": {
                            "filters": {"type": "object"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": world.limits.max_search_results,
                            },
                        },
                        "required": ["filters"],
                        "additionalProperties": False,
                    }
                ),
            ),
            find,
            effect_kind=ToolEffectKind.READ_ONLY,
        ),
    )
