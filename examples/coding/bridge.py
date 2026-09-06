"""Host-side bridge from worker ToolRequest to canonical nested execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from roboagent.message import (
    ArtifactReferenceContent,
    JsonValue,
    freeze_json_object,
    thaw_json,
)
from roboagent.runtime import ToolExecutionContext
from roboagent.tool import ToolExecutionResult, ToolJsonContent, ToolTextContent

from .protocol import CodingProtocolError
from .schema import PythonToolSpec


class CodeToolBridge:
    def __init__(
        self, execution: ToolExecutionContext, specs: tuple[PythonToolSpec, ...]
    ) -> None:
        self.execution = execution
        self.specs = {item.canonical_name: item for item in specs}
        self._responses: dict[str, tuple[dict[str, object], dict[str, object]]] = {}

    async def execute(
        self,
        request_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {"tool_name": tool_name, "arguments": arguments}
        prior = self._responses.get(request_id)
        if prior is not None:
            if prior[0] != payload:
                raise CodingProtocolError(
                    "ipc_protocol_error", "Conflicting duplicate ToolRequest."
                )
            return prior[1]
        if tool_name == "execute_python" or tool_name not in self.specs:
            response = _error(
                "tool_not_allowed", "Tool is not exposed to this Python execution."
            )
        else:
            canonical_arguments = cast(
                Mapping[str, JsonValue], freeze_json_object(arguments)
            )
            result = await self.execution.execute_nested_tool(
                tool_name, canonical_arguments
            )
            response = _tool_result(result)
        self._responses[request_id] = (payload, response)
        return response


def _tool_result(result: ToolExecutionResult) -> dict[str, object]:
    if result.error is not None:
        return _error(result.error.code, result.error.message, result.error.retryable)
    assert result.content is not None
    values = tuple(_content_value(item) for item in result.content)
    if len(values) == 1:
        value = values[0]
    else:
        value = {"kind": "tuple", "value": list(values)}
    return {"ok": True, "value": value}


def _content_value(content: object) -> dict[str, object]:
    if isinstance(content, ToolTextContent):
        return {"kind": "text", "value": content.text}
    if isinstance(content, ToolJsonContent):
        return {"kind": "json", "value": thaw_json(content.value)}
    if isinstance(content, ArtifactReferenceContent):
        return {
            "kind": "artifact",
            "value": {
                "uri": content.uri,
                "media_type": content.media_type,
                "size": content.size,
                "digest": content.digest,
                "preview": content.preview,
            },
        }
    raise CodingProtocolError("executor_failure", "Unsupported canonical ToolContent.")


def _error(code: str, message: str, retryable: bool = False) -> dict[str, object]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
