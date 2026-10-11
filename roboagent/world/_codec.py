"""JSON representation for World data; never opens resource references."""

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from collections.abc import Mapping

from .types import Claim, EntityRef, WorldAnswer, WorldCandidate, WorldEntry


def world_data(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        record = {
            item.name: world_data(getattr(value, item.name)) for item in fields(value)
        }
        if isinstance(value, (Claim, WorldAnswer, WorldCandidate, WorldEntry)):
            # JsonValue can contain any object, including an entity_ref-shaped
            # object. Preserve the closed union's type outside that JSON value.
            record["value_kind"] = (
                "entity_ref" if isinstance(value.value, EntityRef) else "json"
            )
            if isinstance(value.value, EntityRef):
                record["value"] = {"entity_ref": {"id": value.value.id}}
        return record
    if isinstance(value, Mapping):
        return {key: world_data(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [world_data(child) for child in value]
    return value
