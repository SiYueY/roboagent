"""Projection of canonical RoboAgent tools into safe Python callables."""

from __future__ import annotations

import json
import keyword
import re
import unicodedata
from dataclasses import dataclass
from roboagent.message import thaw_json
from roboagent.tool import ToolDefinition

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SCHEMA = frozenset(
    {"oneOf", "anyOf", "allOf", "$ref", "$defs", "patternProperties"}
)
_RESERVED = frozenset(
    {
        "final_answer",
        "RoboAgentToolError",
        "ArtifactHandle",
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "math",
        "statistics",
        "json",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
    }
)


@dataclass(frozen=True, slots=True)
class PythonToolSpec:
    alias: str
    canonical_name: str
    description: str
    properties: tuple[tuple[str, dict[str, object]], ...]
    required: frozenset[str]

    def signature(self) -> str:
        params: list[str] = []
        for name, schema in self.properties:
            if name in self.required and "default" not in schema:
                params.append(name)
            else:
                params.append(f"{name}=...")
        return f"{self.alias}({', '.join(params)}) - {self.description}"

    def arguments(
        self, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> dict[str, object]:
        if len(args) > len(self.properties):
            raise TypeError(f"{self.alias}() received too many positional arguments")
        result: dict[str, object] = {}
        for (name, _), value in zip(self.properties, args, strict=False):
            result[name] = value
        for name, value in kwargs.items():
            if name in result:
                raise TypeError(f"{self.alias}() received multiple values for {name!r}")
            result[name] = value
        known = {name for name, _ in self.properties}
        unknown = set(result) - known
        if unknown:
            raise TypeError(f"{self.alias}() received unknown arguments")
        for name, schema in self.properties:
            if name not in result and "default" in schema:
                result[name] = schema["default"]
        missing = self.required - result.keys()
        if missing:
            raise TypeError(f"{self.alias}() missing required arguments")
        return result


def sanitize_alias(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    if (
        _IDENTIFIER.fullmatch(normalized)
        and not keyword.iskeyword(normalized)
        and not _reserved(normalized)
    ):
        return normalized
    alias = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    alias = re.sub(r"_+", "_", alias)
    if not alias:
        alias = "_tool"
    if alias[0].isdigit():
        alias = "_" + alias
    if keyword.iskeyword(alias):
        alias += "_tool"
    return alias


def project_tools(
    definitions: tuple[ToolDefinition, ...],
) -> tuple[PythonToolSpec, ...]:
    candidates: list[PythonToolSpec] = []
    for definition in definitions:
        if definition.name == "execute_python":
            continue
        normalized_name = unicodedata.normalize("NFKC", definition.name)
        if normalized_name.startswith("__") and normalized_name.endswith("__"):
            continue
        alias = sanitize_alias(definition.name)
        if _reserved(alias):
            continue
        raw = thaw_json(definition.input_schema)
        if not isinstance(raw, dict) or not _supported_object(raw):
            continue
        properties = raw.get("properties", {})
        required = frozenset(raw.get("required", []))
        assert isinstance(properties, dict)
        candidates.append(
            PythonToolSpec(
                alias,
                definition.name,
                definition.description,
                tuple((name, schema) for name, schema in properties.items()),
                required,
            )
        )
    collisions: dict[str, int] = {}
    for item in candidates:
        collisions[item.alias] = collisions.get(item.alias, 0) + 1
    return tuple(item for item in candidates if collisions[item.alias] == 1)


def render_tool_signatures(specs: tuple[PythonToolSpec, ...]) -> str:
    if not specs:
        return "No RoboAgent tools are callable from Python."
    return "Python-callable RoboAgent tools:\n" + "\n".join(
        spec.signature() for spec in specs
    )


def _reserved(name: str) -> bool:
    return name in _RESERVED or name.startswith("__") and name.endswith("__")


def _supported_object(schema: dict[str, object]) -> bool:
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        return False
    if _FORBIDDEN_SCHEMA & schema.keys():
        return False
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return False
    if (
        not all(isinstance(name, str) for name in required)
        or not set(required) <= properties.keys()
    ):
        return False
    return all(_supported_schema(value) for value in properties.values())


def _supported_schema(value: object) -> bool:
    if not isinstance(value, dict) or _FORBIDDEN_SCHEMA & value.keys():
        return False
    schema_type = value.get("type")
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    if not types or not all(
        item in {"string", "integer", "number", "boolean", "null", "array", "object"}
        for item in types
    ):
        return False
    if isinstance(schema_type, list) and (len(types) != 2 or "null" not in types):
        return False
    non_null = next((item for item in types if item != "null"), "null")
    if non_null == "array":
        if "items" not in value or not _supported_schema(value["items"]):
            return False
    if non_null == "object" and not _supported_object(value):
        return False
    if "enum" in value:
        enum = value["enum"]
        if not isinstance(enum, list) or not all(
            _matches_type(item, types) for item in enum
        ):
            return False
    if "default" in value and not _validate_value(value["default"], value):
        return False
    return True


def _matches_type(value: object, types: list[object]) -> bool:
    if value is None:
        return "null" in types
    return any(
        (
            item == "string"
            and isinstance(value, str)
            or item == "integer"
            and type(value) is int
            or item == "number"
            and type(value) in {int, float}
            or item == "boolean"
            and isinstance(value, bool)
            or item == "array"
            and isinstance(value, list)
            or item == "object"
            and isinstance(value, dict)
        )
        for item in types
    )


def _validate_value(value: object, schema: dict[str, object]) -> bool:
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not _matches_type(value, types):
        return False
    if "enum" in schema and value not in schema["enum"]:  # type: ignore[operator]
        return False
    if isinstance(value, list) and "items" in schema:
        return all(_validate_value(item, schema["items"]) for item in value)  # type: ignore[arg-type]
    if isinstance(value, dict) and schema.get("type") == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return False
        if not set(required) <= value.keys() or set(value) - properties.keys():
            return False
        return all(
            _validate_value(item, properties[key]) for key, item in value.items()
        )
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True
