"""Agent-as-Tool session contracts and artifact promotion helpers."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    FileSource,
    FrozenJsonObject,
    ImageContent,
    JsonContent,
    MessageContent,
    TextContent,
)
from roboagent.tool import (
    ArtifactDestination,
    ArtifactReader,
    ToolResultMaterializer,
    Workspace,
)

if TYPE_CHECKING:
    from roboagent.agent import Agent, Session, SessionRepository
    from roboagent.runtime import CancellationToken


@dataclass(frozen=True, slots=True)
class ChildSessionContext:
    root_session_id: str
    workspace: Workspace
    materializer: ToolResultMaterializer
    artifact_reader: ArtifactReader
    artifact_destination: ArtifactDestination
    repository: SessionRepository | None
    diagnostic_metadata: FrozenJsonObject


class ChildSessionFactory(Protocol):
    async def create(self, *, parent: ChildSessionContext, agent: Agent) -> Session: ...


class ChildLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def promote_child_output(
    output: AssistantMessage,
    *,
    reader: ArtifactReader,
    destination: ArtifactDestination,
    cancellation: CancellationToken,
    max_bytes: int,
    chunk_size: int = 64 * 1024,
) -> AssistantMessage:
    promoted: list[MessageContent] = []
    for item in output.content:
        cancellation.raise_if_cancelled()
        if isinstance(item, (TextContent, JsonContent)):
            promoted.append(item)
            continue
        if isinstance(item, ArtifactReferenceContent):
            promoted.append(
                await _copy_artifact(
                    item,
                    reader=reader,
                    destination=destination,
                    cancellation=cancellation,
                    max_bytes=max_bytes,
                    chunk_size=chunk_size,
                )
            )
            continue
        if isinstance(item, (ImageContent, AudioContent, FileContent)):
            source = item.source
            if isinstance(source, BytesSource):
                chunks = (source.data,)
            elif isinstance(source, FileSource):
                chunks = _file_chunks(Path(source.path), chunk_size)
            else:
                raise ChildLifecycleError(
                    "child_output_materialization_failed",
                    "Child output media source cannot be materialized.",
                )
            promoted.append(
                await _write_chunks(
                    chunks,
                    media_type=item.media_type,
                    expected_digest=None,
                    destination=destination,
                    cancellation=cancellation,
                    max_bytes=max_bytes,
                )
            )
            continue
        raise ChildLifecycleError(
            "child_output_materialization_failed",
            "Child output contains unsupported content.",
        )
    return AssistantMessage(
        tuple(promoted), output.tool_calls, timestamp=output.timestamp
    )


async def _copy_artifact(
    reference: ArtifactReferenceContent,
    *,
    reader: ArtifactReader,
    destination: ArtifactDestination,
    cancellation: CancellationToken,
    max_bytes: int,
    chunk_size: int,
) -> ArtifactReferenceContent:
    async def chunks():
        async for chunk in reader.iter_bytes(reference, chunk_size=chunk_size):
            yield chunk

    return await _write_chunks(
        chunks(),
        media_type=reference.media_type,
        expected_digest=reference.digest,
        expected_size=reference.size,
        destination=destination,
        cancellation=cancellation,
        max_bytes=max_bytes,
    )


async def _write_chunks(
    chunks,
    *,
    media_type: str | None,
    expected_digest: str | None,
    expected_size: int | None = None,
    destination: ArtifactDestination,
    cancellation: CancellationToken,
    max_bytes: int,
) -> ArtifactReferenceContent:
    writer = await destination.create_temp(media_type=media_type)
    digest = hashlib.sha256()
    size = 0
    try:
        if hasattr(chunks, "__aiter__"):
            async for chunk in chunks:
                cancellation.raise_if_cancelled()
                if type(chunk) is not bytes:
                    raise ChildLifecycleError(
                        "child_output_materialization_failed",
                        "Artifact reader returned invalid bytes.",
                    )
                size += len(chunk)
                if size > max_bytes:
                    raise ChildLifecycleError(
                        "child_artifact_too_large",
                        "Child artifact exceeds the configured limit.",
                    )
                digest.update(chunk)
                await writer.write(chunk)
        else:
            for chunk in chunks:
                cancellation.raise_if_cancelled()
                size += len(chunk)
                if size > max_bytes:
                    raise ChildLifecycleError(
                        "child_artifact_too_large",
                        "Child artifact exceeds the configured limit.",
                    )
                digest.update(chunk)
                await writer.write(chunk)
        actual = f"sha256:{digest.hexdigest()}"
        if expected_size is not None and size != expected_size:
            raise ChildLifecycleError(
                "child_output_materialization_failed",
                "Child artifact size does not match its reference.",
            )
        if expected_digest is not None and actual != expected_digest:
            raise ChildLifecycleError(
                "child_artifact_digest_mismatch",
                "Child artifact digest does not match.",
            )
        cancellation.raise_if_cancelled()
        published = await writer.publish()
        if published.size != size or published.digest != actual:
            raise ChildLifecycleError(
                "child_output_materialization_failed",
                "Artifact destination returned inconsistent metadata.",
            )
        return published
    except asyncio.CancelledError:
        await writer.abort()
        raise
    except ChildLifecycleError:
        await writer.abort()
        raise
    except Exception as exc:
        await writer.abort()
        raise ChildLifecycleError(
            "child_output_materialization_failed", "Child artifact promotion failed."
        ) from exc


async def _file_chunks(path: Path, chunk_size: int):
    try:
        stream = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)
    except OSError as exc:
        raise ChildLifecycleError(
            "child_output_materialization_failed",
            "Child file output could not be read.",
        ) from exc
