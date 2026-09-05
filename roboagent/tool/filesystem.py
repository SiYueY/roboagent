"""Explicit workspace-scoped filesystem Tool factories."""

from __future__ import annotations

import fnmatch
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from roboagent.message import FrozenJsonObject, canonical_json_dumps

from .tool import (
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolExecutionMode,
    ToolJsonContent,
    ToolTextContent,
)


@dataclass(frozen=True, slots=True)
class FilesystemWorkspace:
    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("FilesystemWorkspace root must be an existing directory.")
        object.__setattr__(self, "root", root)


@dataclass(frozen=True, slots=True)
class FilesystemConfig:
    workspace: FilesystemWorkspace
    max_file_bytes: int = 8 * 1024 * 1024
    max_read_bytes: int = 256 * 1024
    max_write_bytes: int = 8 * 1024 * 1024
    max_list_results: int = 1000
    max_list_output_bytes: int = 256 * 1024
    max_search_results: int = 1000
    max_search_bytes: int = 256 * 1024
    include_hidden: bool = False
    max_depth: int = 32

    def __post_init__(self) -> None:
        numeric = (
            self.max_file_bytes,
            self.max_read_bytes,
            self.max_write_bytes,
            self.max_list_results,
            self.max_list_output_bytes,
            self.max_search_results,
            self.max_search_bytes,
            self.max_depth,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in numeric):
            raise ValueError("Filesystem limits must be positive.")
        if not isinstance(self.workspace, FilesystemWorkspace) or not isinstance(self.include_hidden, bool):
            raise TypeError("FilesystemConfig requires a FilesystemWorkspace and boolean hidden-file policy.")


def create_filesystem_tools(config: FilesystemConfig) -> tuple[Tool, ...]:
    async def read_file(arguments: FrozenJsonObject, _: ToolContext) -> ToolTextContent:
        target, _relative = _target(config.workspace, arguments["path"], allow_file_symlink=True)
        source = _read_regular(target, config.max_file_bytes)
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail("unsupported_file_encoding", "File is not valid UTF-8.", exc)
        offset = arguments.get("offset")
        limit = arguments.get("limit")
        assert offset is None or isinstance(offset, int)
        assert limit is None or isinstance(limit, int)
        selected = text[offset or 0 :] if limit is None else text[offset or 0 : (offset or 0) + limit]
        truncated = len(selected.encode("utf-8")) > config.max_read_bytes
        if truncated:
            selected = selected.encode("utf-8")[: config.max_read_bytes].decode("utf-8", errors="ignore")
        return ToolTextContent(selected, truncated)

    async def write_file(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        target, relative = _target(config.workspace, arguments["path"], for_write=True)
        content = arguments["content"]
        create_parents = arguments.get("create_parents", False)
        assert isinstance(content, str) and isinstance(create_parents, bool)
        encoded = content.encode("utf-8")
        if len(encoded) > config.max_write_bytes:
            _fail("write_too_large", "Content exceeds max_write_bytes.")
        created = not target.exists()
        _atomic_write(config.workspace, target, encoded, create_parents=create_parents)
        return ToolJsonContent(FrozenJsonObject({"path": relative, "bytes_written": len(encoded), "created": created}))

    async def edit_file(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        target, relative = _target(config.workspace, arguments["path"], for_write=True)
        old = arguments["old_text"]
        new = arguments["new_text"]
        assert isinstance(old, str) and isinstance(new, str)
        raw = _read_regular(target, config.max_file_bytes)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail("unsupported_file_encoding", "File is not valid UTF-8.", exc)
        count = text.count(old)
        if count == 0:
            _fail("edit_not_found", "old_text was not found.")
        if count > 1:
            _fail("edit_ambiguous", "old_text occurs more than once.")
        encoded = text.replace(old, new, 1).encode("utf-8")
        if len(encoded) > config.max_write_bytes:
            _fail("write_too_large", "Edited content exceeds max_write_bytes.")
        _atomic_write(config.workspace, target, encoded, create_parents=False)
        return ToolJsonContent(FrozenJsonObject({"path": relative, "bytes_written": len(encoded)}))

    async def list_files(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        target, _ = _target(config.workspace, arguments.get("path", "."))
        if target.is_symlink() or not target.is_dir():
            _fail("not_a_directory", "Path is not a directory.")
        items: list[dict[str, str]] = []
        truncated = False
        for child in sorted(target.iterdir(), key=lambda path: path.name):
            if not config.include_hidden and child.name.startswith("."):
                continue
            kind = "symlink" if child.is_symlink() else "directory" if child.is_dir() else "file"
            candidate = {"name": child.name, "type": kind}
            if len(items) >= config.max_list_results or not _fits({"items": [*items, candidate], "truncated": True}, config.max_list_output_bytes):
                truncated = True
                break
            items.append(candidate)
        return ToolJsonContent(FrozenJsonObject({"items": items, "truncated": truncated}))

    async def find_files(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        pattern = _relative_glob(arguments["pattern"])
        base, _ = _target(config.workspace, arguments.get("path", "."))
        if base.is_symlink():
            _fail("symlink_not_allowed", "Directory symlinks are not traversed.")
        items: list[dict[str, str]] = []
        truncated = False
        for path in _walk(base, config):
            relative = path.relative_to(base).as_posix()
            if not _glob_matches(relative, pattern):
                continue
            candidate = {"path": path.relative_to(config.workspace.root).as_posix()}
            if len(items) >= config.max_search_results or not _fits({"items": [*items, candidate], "truncated": True}, config.max_search_bytes):
                truncated = True
                break
            items.append(candidate)
        return ToolJsonContent(FrozenJsonObject({"items": items, "truncated": truncated}))

    async def search_files(arguments: FrozenJsonObject, _: ToolContext) -> ToolJsonContent:
        query = arguments["query"]
        assert isinstance(query, str)
        base, _ = _target(config.workspace, arguments.get("path", "."))
        if base.is_symlink():
            _fail("symlink_not_allowed", "Directory symlinks are not traversed.")
        pattern = _relative_glob(arguments["glob"]) if arguments.get("glob") is not None else None
        sensitive = arguments.get("case_sensitive", True)
        assert isinstance(sensitive, bool)
        needle = query if sensitive else query.casefold()
        items: list[dict[str, object]] = []
        truncated = False
        for path in _walk(base, config):
            if not path.is_file() or path.is_symlink():
                continue
            relative_base = path.relative_to(base).as_posix()
            if pattern and not _glob_matches(relative_base, pattern):
                continue
            try:
                if path.stat().st_size > config.max_file_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                haystack = line if sensitive else line.casefold()
                start = 0
                while (column := haystack.find(needle, start)) >= 0:
                    candidate = {
                        "path": path.relative_to(config.workspace.root).as_posix(),
                        "line": line_number,
                        "column": column + 1,
                        "text": line,
                    }
                    if len(items) >= config.max_search_results or not _fits({"items": [*items, candidate], "truncated": True}, config.max_search_bytes):
                        truncated = True
                        break
                    items.append(candidate)
                    start = column + max(1, len(needle))
                if truncated:
                    break
            if truncated:
                break
        return ToolJsonContent(FrozenJsonObject({"items": items, "truncated": truncated}))

    def object_schema(properties: object, required: tuple[str, ...] = ()) -> FrozenJsonObject:
        assert isinstance(properties, dict)
        return FrozenJsonObject(
            {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
        )
    path = {"type": "string"}
    integer = {"type": ["integer", "null"], "minimum": 0}
    return (
        Tool(ToolDefinition("read_file", "Read a UTF-8 file from the workspace.", object_schema({"path": path, "offset": integer, "limit": integer}, ("path",))), read_file, ToolExecutionMode.CONCURRENT, ToolEffectKind.READ_ONLY),
        Tool(ToolDefinition("write_file", "Atomically write a UTF-8 file in the workspace.", object_schema({"path": path, "content": {"type": "string"}, "create_parents": {"type": "boolean", "default": False}}, ("path", "content"))), write_file, ToolExecutionMode.SERIAL, ToolEffectKind.SIDE_EFFECTING),
        Tool(ToolDefinition("edit_file", "Atomically replace one exact text occurrence in a workspace file.", object_schema({"path": path, "old_text": {"type": "string", "minLength": 1}, "new_text": {"type": "string"}}, ("path", "old_text", "new_text"))), edit_file, ToolExecutionMode.SERIAL, ToolEffectKind.SIDE_EFFECTING),
        Tool(ToolDefinition("list_files", "List direct children of a workspace directory.", object_schema({"path": {"type": "string", "default": "."}})), list_files, ToolExecutionMode.CONCURRENT, ToolEffectKind.READ_ONLY),
        Tool(ToolDefinition("find_files", "Find workspace paths matching a relative glob.", object_schema({"pattern": path, "path": {"type": "string", "default": "."}}, ("pattern",))), find_files, ToolExecutionMode.CONCURRENT, ToolEffectKind.READ_ONLY),
        Tool(ToolDefinition("search_files", "Search UTF-8 workspace files for literal text.", object_schema({"query": {"type": "string", "minLength": 1}, "path": {"type": "string", "default": "."}, "glob": {"type": ["string", "null"]}, "case_sensitive": {"type": "boolean", "default": True}}, ("query",))), search_files, ToolExecutionMode.CONCURRENT, ToolEffectKind.READ_ONLY),
    )


def validate_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or "\x00" in value:
        _fail("invalid_path", "Path must be a relative string without NUL.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        _fail("invalid_path", "Absolute paths and '..' components are not allowed.")
    return path


def validate_relative_glob(value: object) -> str:
    return _relative_glob(value)


def _relative_glob(value: object) -> str:
    path = validate_relative_path(value)
    result = path.as_posix()
    if not result:
        _fail("invalid_glob", "Glob must not be empty.")
    return result


def _glob_matches(relative: str, pattern: str) -> bool:
    candidates = [pattern]
    while candidates[-1].startswith("**/"):
        candidates.append(candidates[-1][3:])
    path = PurePosixPath(relative)
    return any(path.match(candidate) or fnmatch.fnmatchcase(relative, candidate) for candidate in candidates)


def _target(workspace: FilesystemWorkspace, value: object, *, allow_file_symlink: bool = False, for_write: bool = False) -> tuple[Path, str]:
    relative_path = validate_relative_path(value)
    relative = relative_path.as_posix() or "."
    lexical = workspace.root.joinpath(*relative_path.parts)
    if for_write and lexical.is_symlink():
        _fail("symlink_not_allowed", "Write targets cannot be symlinks.")
    resolved = lexical.resolve(strict=False)
    if not resolved.is_relative_to(workspace.root):
        _fail("path_escape", "Resolved path escapes the workspace.")
    if lexical.is_symlink() and not allow_file_symlink and not for_write:
        return lexical, relative
    return resolved if allow_file_symlink else lexical, relative


def _read_regular(path: Path, maximum: int) -> bytes:
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        _fail("file_not_found", "File does not exist.", exc)
    if not stat.S_ISREG(info.st_mode):
        _fail("not_regular_file", "Path is not a regular file.")
    if info.st_size > maximum:
        _fail("file_too_large", "File exceeds max_file_bytes.")
    try:
        return path.read_bytes()
    except OSError as exc:
        _fail("read_error", "Could not read file.", exc)


def _atomic_write(workspace: FilesystemWorkspace, target: Path, content: bytes, *, create_parents: bool) -> None:
    parent = target.parent
    resolved_parent = parent.resolve(strict=False)
    if not resolved_parent.is_relative_to(workspace.root):
        _fail("path_escape", "Parent directory escapes workspace.")
    if not parent.exists():
        if not create_parents:
            _fail("parent_not_found", "Parent directory does not exist.")
        try:
            parent.mkdir(parents=True)
        except OSError as exc:
            _fail("write_error", "Could not create parent directories.", exc)
    resolved_parent = parent.resolve()
    if target.exists() and (target.is_symlink() or not target.is_file()):
        _fail("not_regular_file", "Write target must be a regular file.")
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=resolved_parent, delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        _fail("write_error", "Could not atomically write file.", exc)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def _walk(base: Path, config: FilesystemConfig):
    if not base.is_dir():
        _fail("not_a_directory", "Search path is not a directory.")
    collected: list[Path] = []
    for root, directories, files in os.walk(base, followlinks=False):
        root_path = Path(root)
        depth = len(root_path.relative_to(base).parts)
        directories[:] = sorted(
            name for name in directories
            if depth < config.max_depth
            and (config.include_hidden or not name.startswith("."))
            and not (root_path / name).is_symlink()
        )
        names = [*directories, *sorted(files)]
        for name in names:
            if not config.include_hidden and name.startswith("."):
                continue
            collected.append(root_path / name)
    yield from sorted(collected, key=lambda path: path.relative_to(base).as_posix())


def _fits(value: object, limit: int) -> bool:
    return len(canonical_json_dumps(value).encode("utf-8")) <= limit


def _fail(code: str, message: str, cause: BaseException | None = None):
    error = ToolExecutionFailure(ToolErrorInfo(code, message))
    if cause is not None:
        raise error from cause
    raise error
