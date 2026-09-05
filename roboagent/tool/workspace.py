"""Artifact workspaces with normalized paths and durable local writes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, Sequence


class WorkspaceError(RuntimeError):
    code = "workspace_error"


class WorkspacePermissionError(WorkspaceError):
    code = "workspace_permission_error"


class WorkspaceMissingError(WorkspaceError):
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
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("WorkspaceEntry.size must be non-negative.")


class Workspace(Protocol):
    @property
    def durable(self) -> bool: ...

    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, data: bytes, *, media_type: str | None = None) -> WorkspaceEntry: ...
    async def stat(self, path: str) -> WorkspaceEntry: ...
    async def list(self, path: str = ".") -> Sequence[WorkspaceEntry]: ...
    async def delete(self, path: str) -> None: ...


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

    async def write(self, path: str, data: bytes, *, media_type: str | None = None) -> WorkspaceEntry:
        key = normalize_workspace_path(path)
        if type(data) is not bytes:
            raise TypeError("Workspace data must be bytes.")
        async with self._lock:
            if key.startswith("blobs/sha256/") and key in self._items and self._items[key][0] != data:
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
            return tuple(_entry(key, data, media_type) for key, (data, media_type) in sorted(self._items.items()) if key.startswith(prefix))

    async def delete(self, path: str) -> None:
        key = normalize_workspace_path(path)
        if key.startswith("blobs/sha256/"):
            raise WorkspacePermissionError("Digest-addressed blobs cannot be deleted directly.")
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

    async def write(self, path: str, data: bytes, *, media_type: str | None = None) -> WorkspaceEntry:
        if type(data) is not bytes:
            raise TypeError("Workspace data must be bytes.")
        return await asyncio.to_thread(self._write, path, data, media_type)

    async def stat(self, path: str) -> WorkspaceEntry:
        return await asyncio.to_thread(self._stat, path)

    async def list(self, path: str = ".") -> Sequence[WorkspaceEntry]:
        return await asyncio.to_thread(self._list, path)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete, path)

    def _target(self, path: str, *, allow_root: bool = False, for_write: bool = False) -> tuple[Path, str]:
        relative = normalize_workspace_path(path, allow_root=allow_root)
        candidate = self.root if relative == "." else self.root.joinpath(*PurePosixPath(relative).parts)
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
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
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
            raise WorkspacePermissionError("Digest-addressed blobs cannot be deleted directly.")
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(path) from exc


def _entry(path: str, data: bytes, media_type: str | None) -> WorkspaceEntry:
    digest = hashlib.sha256(data).hexdigest()
    return WorkspaceEntry(path, len(data), media_type, f"sha256:{digest}")
