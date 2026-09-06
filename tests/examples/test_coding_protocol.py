from __future__ import annotations

import math

import pytest

from examples.coding.protocol import (
    ArtifactHandle,
    CodingProtocolError,
    IpcEnvelope,
    decode_frame,
    encode_frame,
    final_value,
    parse_python_fence,
    validate_direction,
    validate_final_value,
    validate_tool_value,
)
from examples.coding.schema import project_tools, sanitize_alias
from roboagent.message import FrozenJsonObject
from roboagent.tool import ToolDefinition


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_strict_python_fence(newline: str) -> None:
    source = f"reason{newline}````PyThOn  {newline}print(1){newline}````"
    parsed = parse_python_fence(source)
    assert parsed.kind == "python"
    assert parsed.code == f"print(1){newline}"
    assert parsed.text == f"reason{newline}"


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("```python\n```", "empty_python_block"),
        ("```python x\n1\n```", "malformed_python_block"),
        ("```python\n1", "malformed_python_block"),
        ("```python\n1\n```\n```python\n2\n```", "multiple_python_blocks"),
    ],
)
def test_python_fence_errors(source: str, code: str) -> None:
    with pytest.raises(CodingProtocolError) as caught:
        parse_python_fence(source)
    assert caught.value.code == code


def test_non_python_and_python3_are_text() -> None:
    assert parse_python_fence("```javascript\n1\n```").kind == "text"
    assert parse_python_fence("```python3\n1\n```").kind == "text"


def test_final_value_union_is_strict() -> None:
    artifact = ArtifactHandle(
        "workspace://files/a", None, 1, "sha256:" + "a" * 64, None
    )
    assert final_value(None)["kind"] == "empty"
    assert final_value("x")["kind"] == "text"
    assert final_value({"a": [1, True]})["kind"] == "json"
    assert final_value(artifact)["kind"] == "artifact"
    for value in (math.nan, math.inf, object(), lambda: None):
        with pytest.raises(CodingProtocolError) as caught:
            final_value(value)
        assert caught.value.code == "final_answer_not_serializable"
    with pytest.raises(CodingProtocolError) as caught:
        validate_final_value({"kind": "text", "value": 1})
    assert caught.value.code == "invalid_final_envelope"


def test_ipc_frame_identity_direction_and_unknown_fields() -> None:
    message = IpcEnvelope(
        1, "exec", "request", "execute", {"code": "1", "tool_names": [], "extra": True}
    )
    assert decode_frame(encode_frame(message)) == message
    validate_direction(message, "host")
    with pytest.raises(CodingProtocolError):
        validate_direction(message, "worker")
    with pytest.raises(CodingProtocolError):
        IpcEnvelope(1, "x", "", "execute", {})
    with pytest.raises(CodingProtocolError):
        decode_frame(b"\x00\x00\x00\x03{}")


def test_schema_projection_alias_collision_defaults_and_subset() -> None:
    schema = FrozenJsonObject(
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": ["integer", "null"], "default": None},
            },
            "required": ["path"],
            "additionalProperties": False,
        }
    )
    specs = project_tools(
        (
            ToolDefinition("git-diff", "Diff.", schema),
            ToolDefinition("git_diff", "Collision.", schema),
            ToolDefinition("read.file", "Read.", schema),
            ToolDefinition("execute_python", "No recursion.", schema),
        )
    )
    assert [item.alias for item in specs] == ["read_file"]
    assert sanitize_alias("123.tool") == "_123_tool"
    assert sanitize_alias("class") == "class_tool"
    assert sanitize_alias("ｆｏｏ") == "foo"
    assert specs[0].arguments(("a",), {}) == {"path": "a", "limit": None}


def test_unsupported_schema_is_not_exposed() -> None:
    definition = ToolDefinition(
        "free",
        "No.",
        FrozenJsonObject(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        ),
    )
    assert project_tools((definition,)) == ()


def test_tool_value_union_rejects_nested_tuple() -> None:
    validate_tool_value({"kind": "tuple", "value": [{"kind": "text", "value": "x"}]})
    with pytest.raises(CodingProtocolError):
        validate_tool_value(
            {
                "kind": "tuple",
                "value": [{"kind": "tuple", "value": []}],
            }
        )


def test_artifact_handle_requires_canonical_digest_and_uri() -> None:
    with pytest.raises(ValueError):
        ArtifactHandle("https://example.com/a", None, 1, "sha256:" + "a" * 64, None)
    with pytest.raises(ValueError):
        ArtifactHandle("workspace://files/a", None, 1, "missing", None)
