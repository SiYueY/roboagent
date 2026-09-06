"""Artifact workspaces with normalized paths and durable local writes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, Sequence
from uuid import uuid4

from roboagent.message import ArtifactReferenceContent


class WorkspaceError(RuntimeError):
    code = "workspace_error"


class WorkspacePermissionError(WorkspaceError):
    code = "workspace_permission_error"


class WorkspaceMissingError(WorkspaceError):
    code = "workspace_not_found"


class WorkspaceArtifactMissingError(WorkspaceError):
    code = "workspace_artifact_missing"


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    path: str
    size: int
    media_type: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        normalized = normalize_workspace_path(self.path)
        object.__setattr__(self, "path", normalized)
        if (
            not isinstance(self.size, int)
            or isinstance(self.size, bool)
            or self.size < 0
        ):
            raise ValueError("WorkspaceEntry.size must be non-negative.")


class Workspace(Protocol):
    @property
    def durable(self) -> bool: ...

    async def read(self, path: str) -> bytes: ...
    async def write(
        self, path: str, data: bytes, *, media_type: str | None = None
    ) -> WorkspaceEntry: ...
    async def stat(self, path: str) -> WorkspaceEntry: ...
    async def list(self, path: str = ".") -> Sequence[WorkspaceEntry]: ...
    async def delete(self, path: str) -> None: ...


class ArtifactReader(Protocol):
    def iter_bytes(
        self, reference: ArtifactReferenceContent, *, chunk_size: int
    ) -> AsyncIterator[bytes]: ...


class ArtifactWriter(Protocol):
    async def write(self, chunk: bytes) -> None: ...
    async def publish(self) -> ArtifactReferenceContent: ...
    async def abort(self) -> None: ...


class ArtifactDestination(Protocol):
    async def create_temp(self, *, media_type: str | None) -> ArtifactWriter: ...


class WorkspaceArtifactReader:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def iter_bytes(
        self, reference: ArtifactReferenceContent, *, chunk_size: int
    ) -> AsyncIterator[bytes]:
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be positive.")
        path = workspace_path(reference.uri)
        if isinstance(self.workspace, LocalWorkspace):
            target, _ = self.workspace._target(path)
            stream = await asyncio.to_thread(_open_artifact_stream, target)
            try:
                while chunk := await asyncio.to_thread(stream.read, chunk_size):
                    yield chunk
            finally:
                await asyncio.to_thread(stream.close)
        else:
            data = await self.workspace.read(path)
            for offset in range(0, len(data), chunk_size):
                yield data[offset : offset + chunk_size]


class WorkspaceArtifactDestination:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def create_temp(self, *, media_type: str | None) -> ArtifactWriter:
        return _WorkspaceArtifactWriter(self.workspace, media_type)


class _WorkspaceArtifactWriter:
    def __init__(self, workspace: Workspace, media_type: str | None) -> None:
        self.workspace = workspace
        self.media_type = media_type
        self._data = bytearray()
        self._digest = hashlib.sha256()
        self._size = 0
        self._published = False
        self._aborted = False
        self._temporary: Path | None = None
        if isinstance(workspace, LocalWorkspace):
            relative = f"blobs/tmp/artifact-{uuid4().hex}.tmp"
            temporary, _ = workspace._target(relative, for_write=True)
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary, _ = workspace._target(relative, for_write=True)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
            self._temporary = temporary

    async def write(self, chunk: bytes) -> None:
        if type(chunk) is not bytes or self._published or self._aborted:
            raise WorkspaceError("ArtifactWriter is not writable.")
        self._digest.update(chunk)
        self._size += len(chunk)
        if self._temporary is None:
            self._data.extend(chunk)
        else:
            await asyncio.to_thread(self._append, chunk)

    def _append(self, chunk: bytes) -> None:
        assert self._temporary is not None
        descriptor = os.open(
            self._temporary,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        finally:
            os.close(descriptor)

    async def publish(self) -> ArtifactReferenceContent:
        if self._published or self._aborted:
            raise WorkspaceError("ArtifactWriter is already settled.")
        digest = self._digest.hexdigest()
        path = f"blobs/sha256/{digest}"
        if self._temporary is not None and isinstance(self.workspace, LocalWorkspace):
            target, _ = self.workspace._target(path, for_write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target, _ = self.workspace._target(path, for_write=True)
            size, staged_digest = _file_identity(self._temporary)
            if size != self._size or staged_digest != f"sha256:{digest}":
                raise WorkspaceError("Temporary artifact integrity check failed.")
            try:
                os.link(self._temporary, target, follow_symlinks=False)
            except FileExistsError:
                size, existing_digest = _file_identity(target)
                if size != self._size or existing_digest != f"sha256:{digest}":
                    raise WorkspaceError(
                        "Digest-addressed artifact is corrupted."
                    ) from None
            self._temporary.unlink(missing_ok=True)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            entry = await self.workspace.write(
                path, bytes(self._data), media_type=self.media_type
            )
            if entry.size != self._size or entry.digest != f"sha256:{digest}":
                raise WorkspaceError(
                    "Artifact destination returned inconsistent metadata."
                )
        self._published = True
        return ArtifactReferenceContent(
            workspace_uri(path), self.media_type, self._size, f"sha256:{digest}", None
        )

    async def abort(self) -> None:
        if self._published:
            return
        self._aborted = True
        if self._temporary is not None:
            await asyncio.to_thread(self._temporary.unlink, missing_ok=True)


def normalize_workspace_path(path: str, *, allow_root: bool = False) -> str:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise WorkspacePermissionError("Invalid workspace path.")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise WorkspacePermissionError("Workspace path escapes its root.")
    normalized = pure.as_posix()
    if normalized == "." and not allow_root:
        raise WorkspacePermissionError("Workspace root is not a file.")
    return normalized


def workspace_uri(path: str) -> str:
    normalized = normalize_workspace_path(path)
    return f"workspace://{normalized}"


def workspace_path(uri: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(uri) if isinstance(uri, str) else None
    if (
        parsed is None
        or parsed.scheme != "workspace"
        or parsed.netloc not in {"blobs", "files"}
        or parsed.query
        or parsed.fragment
        or parsed.params
        or "%" in uri
        or "//" in parsed.path
        or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
    ):
        raise WorkspacePermissionError("Invalid workspace artifact URI.")
    return normalize_workspace_path(f"{parsed.netloc}{parsed.path}")


async def read_artifact(
    workspace: Workspace, artifact: ArtifactReferenceContent
) -> bytes:
    if not isinstance(artifact, ArtifactReferenceContent):
        raise TypeError("artifact must be ArtifactReferenceContent.")
    try:
        data = await workspace.read(workspace_path(artifact.uri))
    except WorkspaceMissingError as exc:
        raise WorkspaceArtifactMissingError("Workspace artifact is missing.") from exc
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if len(data) != artifact.size or digest != artifact.digest:
        raise WorkspaceError("Workspace artifact integrity check failed.")
    return data


class InMemoryWorkspace:
    durable = False

    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, str | None]] = {}
        self._lock = asyncio.Lock()

    async def read(self, path: str) -> bytes:
        key = normalize_workspace_path(path)
        async with self._lock:
            try:
                return bytes(self._items[key][0])
            except KeyError as exc:
                raise WorkspaceMissingError(key) from exc

    async def write(
        self, path: str, data: bytes, *, media_type: str | None = None
    ) -> WorkspaceEntry:
        key = normalize_workspace_path(path)
        if type(data) is not bytes:
            raise TypeError("Workspace data must be bytes.")
        async with self._lock:
            if (
                key.startswith("blobs/sha256/")
                and key in self._items
                and self._items[key][0] != data
            ):
                raise WorkspaceError("Digest-addressed blobs are immutable.")
            self._items[key] = (bytes(data), media_type)
        return _entry(key, data, media_type)

    async def stat(self, path: str) -> WorkspaceEntry:
        key = normalize_workspace_path(path)
        async with self._lock:
            try:
                data, media_type = self._items[key]
            except KeyError as exc:
                raise WorkspaceMissingError(key) from exc
        return _entry(key, data, media_type)

    async def list(self, path: str = ".") -> Sequence[WorkspaceEntry]:
        prefix = normalize_workspace_path(path, allow_root=True)
        prefix = "" if prefix == "." else prefix.rstrip("/") + "/"
        async with self._lock:
            return tuple(
                _entry(key, data, media_type)
                for key, (data, media_type) in sorted(self._items.items())
                if key.startswith(prefix)
            )

    async def delete(self, path: str) -> None:
        key = normalize_workspace_path(path)
        if key.startswith("blobs/sha256/"):
            raise WorkspacePermissionError(
                "Digest-addressed blobs cannot be deleted directly."
            )
        async with self._lock:
            try:
                del self._items[key]
            except KeyError as exc:
                raise WorkspaceMissingError(key) from exc


class LocalWorkspace:
    durable = True

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("LocalWorkspace root must be a directory.")

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread(self._read, path)

    async def write(
        self, path: str, data: bytes, *, media_type: str | None = None
    ) -> WorkspaceEntry:
        if type(data) is not bytes:
            raise TypeError("Workspace data must be bytes.")
        return await asyncio.to_thread(self._write, path, data, media_type)

    async def stat(self, path: str) -> WorkspaceEntry:
        return await asyncio.to_thread(self._stat, path)

    async def list(self, path: str = ".") -> Sequence[WorkspaceEntry]:
        return await asyncio.to_thread(self._list, path)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete, path)

    def _target(
        self, path: str, *, allow_root: bool = False, for_write: bool = False
    ) -> tuple[Path, str]:
        relative = normalize_workspace_path(path, allow_root=allow_root)
        candidate = (
            self.root
            if relative == "."
            else self.root.joinpath(*PurePosixPath(relative).parts)
        )
        check = candidate.parent.resolve() if for_write else candidate.resolve()
        if check != self.root and self.root not in check.parents:
            raise WorkspacePermissionError("Workspace path escapes through a symlink.")
        if not for_write and candidate.is_symlink():
            raise WorkspacePermissionError("Workspace symlinks are not allowed.")
        return candidate, relative

    def _read(self, path: str) -> bytes:
        target, _ = self._target(path)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(path) from exc

    def _write(self, path: str, data: bytes, media_type: str | None) -> WorkspaceEntry:
        target, relative = self._target(path, for_write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target, relative = self._target(relative, for_write=True)
        if target.exists() and target.is_symlink():
            raise WorkspacePermissionError("Workspace symlinks are not allowed.")
        if relative.startswith("blobs/sha256/") and target.exists():
            existing = target.read_bytes()
            if existing != data:
                raise WorkspaceError("Digest-addressed blobs are immutable.")
            return _entry(relative, existing, media_type)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return _entry(relative, data, media_type)

    def _stat(self, path: str) -> WorkspaceEntry:
        target, relative = self._target(path)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(path) from exc
        return _entry(relative, data, None)

    def _list(self, path: str) -> Sequence[WorkspaceEntry]:
        target, relative = self._target(path, allow_root=True)
        if not target.is_dir():
            raise WorkspaceMissingError(path)
        result = []
        for child in sorted(target.rglob("*")):
            if child.is_symlink():
                continue
            if child.is_file():
                child_relative = child.relative_to(self.root).as_posix()
                result.append(_entry(child_relative, child.read_bytes(), None))
        return tuple(result)

    def _delete(self, path: str) -> None:
        target, relative = self._target(path)
        if relative.startswith("blobs/sha256/"):
            raise WorkspacePermissionError(
                "Digest-addressed blobs cannot be deleted directly."
            )
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(path) from exc


def _entry(path: str, data: bytes, media_type: str | None) -> WorkspaceEntry:
    digest = hashlib.sha256(data).hexdigest()
    return WorkspaceEntry(path, len(data), media_type, f"sha256:{digest}")


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("Digest-addressed artifact is not a regular file.")
        while chunk := os.read(descriptor, 64 * 1024):
            size += len(chunk)
            digest.update(chunk)
    except OSError as exc:
        raise WorkspaceError(
            "Digest-addressed artifact could not be verified."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return size, f"sha256:{digest.hexdigest()}"


def _open_artifact_stream(path: Path) -> BinaryIO:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WorkspaceError("Workspace artifact is not a regular file.")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    except OSError as exc:
        raise WorkspaceError("Workspace artifact could not be opened safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
