"""One-shot conversion of raw tool output into bounded canonical content."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from roboagent.message import ArtifactReferenceContent, FrozenJsonObject, ToolCall, canonical_json_dumps
from roboagent.runtime.types import CancellationToken

from .tool import (
    BinaryToolContent,
    RawToolResult,
    ResourceToolContent,
    ToolContent,
    ToolContext,
    ToolJsonContent,
    ToolTextContent,
)
from .workspace import Workspace, workspace_uri


class ToolMaterializationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolOutputLimits:
    max_raw_bytes: int = 8 * 1024 * 1024
    max_inline_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (self.max_raw_bytes, self.max_inline_bytes)):
            raise TypeError("Tool output limits must be integers.")
        if not 0 < self.max_inline_bytes <= self.max_raw_bytes:
            raise ValueError("Tool output limits require 0 < max_inline_bytes <= max_raw_bytes.")


class ToolResultMaterializer(Protocol):
    async def materialize(
        self,
        raw: RawToolResult,
        *,
        call: ToolCall,
        context: ToolContext,
        cancellation: CancellationToken,
    ) -> tuple[ToolContent, ...]: ...


class InlineToolResultMaterializer:
    def __init__(self, limits: ToolOutputLimits | None = None) -> None:
        self.limits = limits or ToolOutputLimits(max_raw_bytes=64 * 1024, max_inline_bytes=64 * 1024)

    async def materialize(self, raw, *, call, context, cancellation):
        cancellation.raise_if_cancelled()
        size = raw_result_size(raw)
        if size > self.limits.max_raw_bytes:
            raise ToolMaterializationError("tool_output_too_large", "Tool output exceeds max_raw_bytes.")
        if size > self.limits.max_inline_bytes:
            raise ToolMaterializationError("tool_materialization_error", "Tool output requires a Workspace.")
        result: list[ToolContent] = []
        for item in raw.content:
            if isinstance(item, (ToolTextContent, ToolJsonContent)):
                result.append(item)
            elif isinstance(item, ResourceToolContent) and item.data is None:
                raise ToolMaterializationError(
                    "workspace_artifact_missing", "Remote resource bytes are unavailable."
                )
            else:
                raise ToolMaterializationError("tool_materialization_error", "Binary content requires a Workspace.")
        cancellation.raise_if_cancelled()
        return tuple(result)


class WorkspaceToolResultMaterializer:
    def __init__(self, *, workspace: Workspace, limits: ToolOutputLimits | None = None) -> None:
        self.workspace = workspace
        self.limits = limits or ToolOutputLimits()

    async def materialize(self, raw, *, call, context, cancellation):
        cancellation.raise_if_cancelled()
        size = raw_result_size(raw)
        if size > self.limits.max_raw_bytes:
            raise ToolMaterializationError("tool_output_too_large", "Tool output exceeds max_raw_bytes.")
        inline_all = size <= self.limits.max_inline_bytes
        result: list[ToolContent] = []
        for item in raw.content:
            cancellation.raise_if_cancelled()
            if isinstance(item, ToolTextContent) and inline_all:
                result.append(item)
            elif isinstance(item, ToolJsonContent) and inline_all:
                result.append(item)
            elif isinstance(item, ResourceToolContent) and item.data is None:
                raise ToolMaterializationError(
                    "workspace_artifact_missing", "Remote resource bytes are unavailable."
                )
            else:
                data, media_type, preview = _bytes(item)
                digest = hashlib.sha256(data).hexdigest()
                path = f"blobs/sha256/{digest}"
                entry = await self.workspace.write(path, data, media_type=media_type)
                if entry.path != path or entry.size != len(data) or entry.digest != f"sha256:{digest}":
                    raise ToolMaterializationError(
                        "tool_materialization_error", "Workspace returned inconsistent artifact metadata."
                    )
                result.append(
                    ArtifactReferenceContent(
                        workspace_uri(entry.path),
                        media_type,
                        entry.size,
                        f"sha256:{digest}",
                        preview,
                    )
                )
        cancellation.raise_if_cancelled()
        return tuple(result)


def raw_result_size(raw: RawToolResult) -> int:
    total = 0
    for item in raw.content:
        total += 1  # stable per-block framing prevents unbounded zero-byte block lists
        if isinstance(item, ToolTextContent):
            total += len(item.text.encode("utf-8"))
        elif isinstance(item, ToolJsonContent):
            total += len(canonical_json_dumps(item.value).encode("utf-8"))
        elif isinstance(item, BinaryToolContent):
            total += len(item.data)
        elif isinstance(item, ResourceToolContent):
            total += len(item.data) if item.data is not None else len(item.uri.encode("utf-8"))
    return total


def raw_result_evidence(raw: RawToolResult, *, max_preview_bytes: int = 1024) -> ToolContent:
    digest = hashlib.sha256()
    kinds: list[str] = []
    preview = ""
    for item in raw.content:
        kind = type(item).__name__
        digest.update(kind.encode("ascii"))
        if isinstance(item, ToolTextContent):
            payload = item.text.encode("utf-8")
            if not preview:
                preview = item.text
        elif isinstance(item, ToolJsonContent):
            payload = canonical_json_dumps(item.value).encode("utf-8")
            if not preview:
                preview = payload.decode("utf-8")
        elif isinstance(item, BinaryToolContent):
            payload = item.data
        else:
            payload = item.data if item.data is not None else item.uri.encode("utf-8")
        if len(kinds) < 64:
            kinds.append(kind)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    bounded = preview.encode("utf-8")[:max_preview_bytes].decode("utf-8", errors="ignore")
    return ToolJsonContent(
        FrozenJsonObject(
            {
            "raw_digest": f"sha256:{digest.hexdigest()}",
            "block_count": len(raw.content),
            "block_kinds": kinds,
            "block_kinds_truncated": len(raw.content) > len(kinds),
            "preview": bounded or None,
            }
        )
    )


def _bytes(item: object) -> tuple[bytes, str | None, str | None]:
    if isinstance(item, ToolTextContent):
        data = item.text.encode("utf-8")
        return data, "text/plain", _preview(item.text)
    if isinstance(item, ToolJsonContent):
        text = canonical_json_dumps(item.value)
        return text.encode("utf-8"), "application/json", _preview(text)
    if isinstance(item, BinaryToolContent):
        return item.data, item.media_type, None
    if isinstance(item, ResourceToolContent) and item.data is not None:
        return item.data, item.media_type, None
    raise ToolMaterializationError("tool_materialization_error", "Resource bytes are unavailable.")


def _preview(text: str) -> str | None:
    encoded = text.encode("utf-8")[:4096]
    return encoded.decode("utf-8", errors="ignore") or None
