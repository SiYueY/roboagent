"""Transactional POSIX apply_patch builtin."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from roboagent.message import FrozenJsonObject, canonical_json_dumps
from roboagent.runtime import (
    ExecutionRecordStatus,
    SettlementError,
    SupplementalExecutionRecord,
)

from .filesystem import FilesystemConfig
from .tool import (
    CompositeToolExecutionFailure,
    _CompositeToolCancellation,
    CompositeToolOutcome,
    EffectCertainty,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolEffectRecord,
    ToolEffectReporting,
    ToolEffectStatus,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolJsonContent,
)


@dataclass(frozen=True, slots=True)
class ApplyPatchConfig:
    filesystem: FilesystemConfig
    max_patch_bytes: int = 256 * 1024
    max_files: int = 64
    max_file_bytes: int = 4 * 1024 * 1024
    max_result_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.filesystem, FilesystemConfig):
            raise TypeError("filesystem must be FilesystemConfig.")
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.max_patch_bytes,
                self.max_files,
                self.max_file_bytes,
                self.max_result_bytes,
            )
        ):
            raise ValueError("apply_patch limits must be positive integers.")


@dataclass(frozen=True, slots=True)
class _PatchLine:
    kind: str
    text: str
    no_newline: bool = False


@dataclass(frozen=True, slots=True)
class _Section:
    operation: str
    path: str
    body: tuple[str, ...]


@dataclass(slots=True)
class _Plan:
    operation: str
    path: str
    target: Path
    before: bytes | None
    after: bytes | None
    mode: int | None
    before_digest: str | None
    after_digest: str | None
    staged: Path | None = None


def create_apply_patch_tool(config: ApplyPatchConfig) -> Tool:
    async def apply(
        arguments: FrozenJsonObject, context: ToolContext
    ) -> CompositeToolOutcome:
        if os.name != "posix":
            _fail(
                "apply_patch_platform_unsupported",
                "V1.3 apply_patch requires a POSIX platform.",
            )
        patch = arguments["patch"]
        assert isinstance(patch, str)
        try:
            encoded_patch = patch.encode("utf-8")
        except UnicodeEncodeError as exc:
            _fail("unsupported_binary_content", "Patch is not valid UTF-8 text.", exc)
        if len(encoded_patch) > config.max_patch_bytes:
            _fail("patch_too_large", "Patch exceeds max_patch_bytes.")
        sections = _parse_patch(patch)
        if len(sections) > config.max_files:
            _fail("too_many_patch_files", "Patch exceeds max_files.")
        plans = _prepare(config, sections)
        result = _bounded_result(plans, config.max_result_bytes)
        _revalidate(config, plans)
        _stage(plans)
        handler = _PatchSettlement(plans)
        try:
            # Staging itself creates a race window, so the complete target and
            # parent assumptions are checked once more immediately before the
            # settlement barrier owns commit/rollback.
            _revalidate(config, plans)
        except BaseException:
            handler.cleanup_staged()
            raise
        if context.execution is None:
            handler.cleanup_staged()
            _fail(
                "nested_execution_unavailable",
                "apply_patch requires V1.3 execution context.",
            )
        try:
            context.cancellation.raise_if_cancelled()
        except asyncio.CancelledError:
            handler.cleanup_staged()
            raise
        try:
            async with context.execution.settlement_barrier(handler=handler):
                try:
                    handler.commit()
                except Exception as exc:
                    handler.failure = exc
        except SettlementError as exc:
            if any(
                state in {"committed", "unknown"} for state in handler.states.values()
            ):
                raise CompositeToolExecutionFailure(
                    ToolErrorInfo(
                        "patch_rollback_failed", "Patch rollback was incomplete."
                    ),
                    effects=handler.failure_effects(context),
                    records=(_rollback_record(handler, plans),),
                ) from exc
            if handler.failure is not None:
                _fail("patch_commit_failed", "Patch commit failed.", handler.failure)
            raise
        except asyncio.CancelledError as exc:
            effects: tuple[ToolEffectRecord, ...] = ()
            records: tuple[SupplementalExecutionRecord, ...] = ()
            if (
                handler.failure is None
                and not handler.forced
                and len(handler.committed) == len(plans)
            ):
                effects = tuple(_success_effect(plan, context) for plan in plans)
            elif any(
                state in {"committed", "unknown"} for state in handler.states.values()
            ):
                effects = handler.failure_effects(context)
                records = (_rollback_record(handler, plans),)
            raise _CompositeToolCancellation(effects=effects, records=records) from exc
        if any(state in {"committed", "unknown"} for state in handler.states.values()):
            effects = handler.failure_effects(context)
            raise CompositeToolExecutionFailure(
                ToolErrorInfo(
                    "patch_rollback_failed", "Patch rollback was incomplete."
                ),
                effects=effects,
                records=(_rollback_record(handler, plans),),
            )
        if handler.forced:
            _fail("patch_commit_failed", "Patch settlement required a forced rollback.")
        if handler.failure is not None:
            _fail("patch_commit_failed", "Patch commit failed.", handler.failure)
        effects = tuple(_success_effect(plan, context) for plan in plans)
        return CompositeToolOutcome((ToolJsonContent(result),), effects=effects)

    return Tool(
        ToolDefinition(
            "apply_patch",
            "Apply a structured UTF-8 text patch to files inside the configured workspace.",
            FrozenJsonObject(
                {
                    "type": "object",
                    "properties": {"patch": {"type": "string", "minLength": 1}},
                    "required": ["patch"],
                    "additionalProperties": False,
                }
            ),
        ),
        apply,
        effect_kind=ToolEffectKind.SIDE_EFFECTING,
        effect_reporting=ToolEffectReporting.COMPOSITE,
    )


def _parse_patch(patch: str) -> tuple[_Section, ...]:
    if "\x00" in patch:
        _fail("unsupported_binary_content", "Patch contains NUL.")
    if "\r" in patch.replace("\r\n", ""):
        _fail("invalid_patch", "Patch contains unsupported line endings.")
    normalized = patch.replace("\r\n", "\n")
    if not (
        normalized.endswith("*** End Patch") or normalized.endswith("*** End Patch\n")
    ):
        _fail("invalid_patch", "Patch must end at the End Patch marker.")
    if normalized.endswith("*** End Patch\n"):
        normalized = normalized[:-1]
    lines = normalized.split("\n")
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        _fail("invalid_patch", "Patch container markers are invalid.")
    sections: list[_Section] = []
    index = 1
    headers = {
        "*** Add File: ": "added",
        "*** Update File: ": "modified",
        "*** Delete File: ": "deleted",
    }
    while index < len(lines) - 1:
        header = lines[index]
        match = next(
            (
                (prefix, operation)
                for prefix, operation in headers.items()
                if header.startswith(prefix)
            ),
            None,
        )
        if match is None:
            _fail("invalid_patch", "Expected an exact patch section header.")
        prefix, operation = match
        path = _validate_path(header[len(prefix) :])
        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not any(
            lines[index].startswith(prefix) for prefix in headers
        ):
            body.append(lines[index])
            index += 1
        sections.append(_Section(operation, path, tuple(body)))
    if not sections:
        _fail("invalid_patch", "Patch requires at least one section.")
    paths = [section.path for section in sections]
    if len(paths) != len(set(paths)):
        _fail("duplicate_patch_target", "Patch target occurs more than once.")
    for section in sections:
        if section.operation == "added":
            _parse_add(section.body)
        elif section.operation == "deleted":
            if section.body:
                _fail("invalid_patch", "Delete section cannot contain a body.")
        else:
            _parse_hunks(section.body)
    return tuple(sections)


def _validate_path(path: str) -> str:
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        _fail("invalid_path", "Patch path is not valid UTF-8.", exc)
    if (
        not path
        or path.startswith("/")
        or path.startswith((" ", "\t"))
        or path.endswith(("/", " ", "\t"))
        or "//" in path
        or "\n" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        _fail("invalid_path", "Patch path is not a strict relative path.")
    return path


def _prepare(
    config: ApplyPatchConfig, sections: tuple[_Section, ...]
) -> tuple[_Plan, ...]:
    root = config.filesystem.workspace.root
    plans: list[_Plan] = []
    inodes: set[tuple[int, int]] = set()
    platform_paths: set[str] = set()
    for section in sections:
        target = root.joinpath(*section.path.split("/"))
        platform_path = os.path.normcase(os.fspath(target))
        if platform_path in platform_paths:
            _fail(
                "duplicate_patch_target",
                "Patch targets alias under platform path semantics.",
            )
        platform_paths.add(platform_path)
        _validate_parent(root, target.parent)
        exists = target.exists()
        if target.is_symlink():
            _fail("symlink_not_allowed", "Patch targets cannot be symlinks.")
        before: bytes | None = None
        mode: int | None = None
        if section.operation == "added":
            if exists:
                _fail("patch_target_exists", "Add target already exists.")
            if not target.parent.is_dir():
                _fail("parent_directory_missing", "Add target parent does not exist.")
            after = _parse_add(section.body)
        else:
            if not exists:
                _fail("patch_target_missing", "Patch target does not exist.")
            info = target.stat()
            if not stat.S_ISREG(info.st_mode):
                _fail(
                    "unsupported_binary_content",
                    "Patch target is not a regular text file.",
                )
            if info.st_size > config.max_file_bytes:
                _fail("patch_file_too_large", "Patch target exceeds max_file_bytes.")
            identity = (info.st_dev, info.st_ino)
            if identity in inodes:
                _fail("duplicate_patch_target", "Patch targets alias the same file.")
            inodes.add(identity)
            before = target.read_bytes()
            _validate_text(before)
            mode = stat.S_IMODE(info.st_mode)
            if section.operation == "deleted":
                if section.body:
                    _fail("invalid_patch", "Delete section cannot contain a body.")
                after = None
            else:
                after = _apply_update(before, section.body)
        if max(len(before or b""), len(after or b"")) > config.max_file_bytes:
            _fail("patch_file_too_large", "Patch target exceeds max_file_bytes.")
        plans.append(
            _Plan(
                section.operation,
                section.path,
                target,
                before,
                after,
                mode,
                _digest(before),
                _digest(after),
            )
        )
    return tuple(plans)


def _parse_add(body: tuple[str, ...]) -> bytes:
    values: list[tuple[str, bool]] = []
    for line in body:
        if line == "\\ No newline at end of file":
            if not values or values[-1][1]:
                _fail("invalid_patch", "No-newline marker is misplaced.")
            text, _ = values[-1]
            values[-1] = (text, True)
        elif line.startswith("+"):
            values.append((line[1:], False))
        else:
            _fail("invalid_patch", "Add lines must begin with '+'.")
    if any(no_newline for _, no_newline in values[:-1]):
        _fail("invalid_patch", "No-newline marker must describe the final added line.")
    try:
        return "".join(
            text + ("" if no_newline else "\n") for text, no_newline in values
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        _fail("unsupported_binary_content", "Patch content is not valid UTF-8.", exc)


def _parse_hunks(body: tuple[str, ...]) -> tuple[tuple[_PatchLine, ...], ...]:
    hunks: list[list[_PatchLine]] = []
    for line in body:
        if line.rstrip() == "@@" and line.startswith("@@"):
            hunks.append([])
        elif line == "\\ No newline at end of file":
            if not hunks or not hunks[-1] or hunks[-1][-1].no_newline:
                _fail("invalid_patch", "No-newline marker is misplaced.")
            previous = hunks[-1][-1]
            hunks[-1][-1] = _PatchLine(previous.kind, previous.text, True)
        elif hunks and line[:1] in {" ", "+", "-"}:
            hunks[-1].append(_PatchLine(line[0], line[1:]))
        else:
            _fail("invalid_patch", "Update body contains invalid hunk syntax.")
    if not hunks or any(not hunk for hunk in hunks):
        _fail("invalid_patch", "Update requires non-empty hunks.")
    return tuple(tuple(hunk) for hunk in hunks)


def _file_lines(data: bytes) -> list[tuple[str, str]]:
    text = data.decode("utf-8")
    result = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            result.append((line[:-2], "\r\n"))
        elif line.endswith("\n"):
            result.append((line[:-1], "\n"))
        else:
            result.append((line, ""))
    return result


def _apply_update(before: bytes, body: tuple[str, ...]) -> bytes:
    lines = _file_lines(before)
    newline = _dominant_newline(lines)
    for hunk in _parse_hunks(body):
        old = tuple(line for line in hunk if line.kind in {" ", "-"})
        matches = []
        for start in range(len(lines) - len(old) + 1):
            candidate = lines[start : start + len(old)]
            if all(
                candidate[index][0] == expected.text
                and (
                    candidate[index][1] == ""
                    if expected.no_newline
                    else candidate[index][1] != ""
                )
                for index, expected in enumerate(old)
            ):
                matches.append(start)
        if len(matches) != 1:
            _fail("patch_conflict", "Patch hunk does not match uniquely.")
        start = matches[0]
        replacement: list[tuple[str, str]] = []
        old_index = start
        for line in hunk:
            if line.kind == " ":
                replacement.append(lines[old_index])
                old_index += 1
            elif line.kind == "-":
                old_index += 1
            else:
                replacement.append((line.text, "" if line.no_newline else newline))
        lines[start : start + len(old)] = replacement
    return "".join(text + ending for text, ending in lines).encode("utf-8")


def _dominant_newline(lines: list[tuple[str, str]]) -> str:
    endings = [ending for _, ending in lines if ending]
    lf = endings.count("\n")
    crlf = endings.count("\r\n")
    if lf > crlf:
        return "\n"
    if crlf > lf:
        return "\r\n"
    return endings[0] if endings else "\n"


def _validate_text(data: bytes) -> None:
    if data.startswith(b"\xef\xbb\xbf") or b"\x00" in data:
        _fail("unsupported_binary_content", "Patch target is not plain UTF-8 text.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("unsupported_binary_content", "Patch target is not valid UTF-8.", exc)


def _validate_parent(root: Path, parent: Path, *, changed: bool = False) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError:
        _fail("path_escape", "Patch path escapes the workspace.")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            _fail(
                "patch_target_changed" if changed else "symlink_not_allowed",
                "Patch parent cannot be a symlink.",
            )


def _revalidate(config: ApplyPatchConfig, plans: tuple[_Plan, ...]) -> None:
    root = config.filesystem.workspace.root
    identities: set[tuple[int, int]] = set()
    for plan in plans:
        _validate_parent(root, plan.target.parent, changed=True)
        if plan.target.is_symlink():
            _fail("patch_target_changed", "Patch target changed before commit.")
        if plan.operation == "added":
            if plan.target.exists() or not plan.target.parent.is_dir():
                _fail("patch_target_changed", "Add target changed before commit.")
        else:
            try:
                data = plan.target.read_bytes()
                info = plan.target.stat()
            except OSError as exc:
                _fail(
                    "patch_target_changed", "Patch target changed before commit.", exc
                )
            if _digest(data) != plan.before_digest:
                _fail("patch_target_changed", "Patch target changed before commit.")
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != plan.mode
            ):
                _fail("patch_target_changed", "Patch target changed before commit.")
            identity = (info.st_dev, info.st_ino)
            if identity in identities:
                _fail("duplicate_patch_target", "Patch targets alias the same file.")
            identities.add(identity)


def _stage(plans: tuple[_Plan, ...]) -> None:
    try:
        for plan in plans:
            if plan.after is None:
                continue
            temporary: str | Path
            if plan.operation == "added":
                temporary = plan.target.parent / (
                    f".{plan.target.name}.{uuid4().hex}.patch"
                )
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o644,
                )
                plan.mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            else:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{plan.target.name}.",
                    suffix=".patch",
                    dir=plan.target.parent,
                )
            plan.staged = Path(temporary)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(plan.after)
                stream.flush()
                os.fsync(stream.fileno())
            assert plan.mode is not None
            if plan.operation != "added":
                os.chmod(plan.staged, plan.mode)
    except OSError as exc:
        for plan in plans:
            if plan.staged is not None:
                plan.staged.unlink(missing_ok=True)
        _fail("patch_stage_failed", "Patch staging failed.", exc)


class _PatchSettlement:
    def __init__(self, plans: tuple[_Plan, ...]) -> None:
        self.plans = plans
        self.committed: list[_Plan] = []
        self.failure: Exception | None = None
        self.states: dict[str, str] = {}
        self.forced = False

    def commit(self) -> None:
        for plan in self.plans:
            if plan.operation == "added":
                assert plan.staged is not None
                os.link(plan.staged, plan.target)
                plan.staged.unlink()
                plan.staged = None
            elif plan.operation == "modified":
                assert plan.staged is not None
                os.replace(plan.staged, plan.target)
                plan.staged = None
            else:
                plan.target.unlink()
            self.committed.append(plan)

    async def settle(self) -> None:
        if self.failure is not None:
            self.rollback()
        self.cleanup_staged()
        self._raise_if_incomplete()

    async def force_settle(self) -> None:
        self.forced = True
        self.rollback()
        self.cleanup_staged()
        self._raise_if_incomplete()

    def _raise_if_incomplete(self) -> None:
        if any(state in {"committed", "unknown"} for state in self.states.values()):
            raise _PatchRollbackIncomplete(
                "Patch rollback did not restore all targets."
            )

    def rollback(self) -> None:
        for plan in reversed(self.committed):
            if self.states.get(plan.path) in {"restored", "unchanged"}:
                continue
            try:
                if plan.operation == "added":
                    if (
                        plan.target.exists()
                        and _digest(plan.target.read_bytes()) == plan.after_digest
                    ):
                        plan.target.unlink()
                        self.states[plan.path] = "restored"
                    else:
                        self.states[plan.path] = "unknown"
                elif plan.operation == "modified":
                    if (
                        plan.target.exists()
                        and _digest(plan.target.read_bytes()) == plan.after_digest
                    ):
                        assert plan.before is not None and plan.mode is not None
                        _restore(plan.target, plan.before, plan.mode)
                        self.states[plan.path] = "restored"
                    else:
                        self.states[plan.path] = "unknown"
                else:
                    if not plan.target.exists():
                        assert plan.before is not None and plan.mode is not None
                        _restore(plan.target, plan.before, plan.mode)
                        self.states[plan.path] = "restored"
                    elif _digest(plan.target.read_bytes()) == plan.before_digest:
                        self.states[plan.path] = "unchanged"
                    else:
                        self.states[plan.path] = "unknown"
            except OSError:
                try:
                    current = (
                        _digest(plan.target.read_bytes())
                        if plan.target.exists()
                        else None
                    )
                    self.states[plan.path] = (
                        "committed" if current == plan.after_digest else "unknown"
                    )
                except OSError:
                    self.states[plan.path] = "unknown"
        for plan in self.plans:
            self.states.setdefault(plan.path, "unchanged")

    def cleanup_staged(self) -> None:
        for plan in self.plans:
            if plan.staged is not None:
                plan.staged.unlink(missing_ok=True)
                plan.staged = None

    def failure_effects(self, context: ToolContext) -> tuple[ToolEffectRecord, ...]:
        effects = []
        for plan in self.plans:
            state = self.states.get(plan.path, "unknown")
            if state not in {"committed", "unknown"}:
                continue
            effects.append(
                ToolEffectRecord(
                    context.execution.lineage.tool_call_id or "apply_patch",  # type: ignore[union-attr]
                    "apply_patch",
                    ToolEffectKind.SIDE_EFFECTING,
                    ToolEffectStatus.SUCCEEDED
                    if state == "committed"
                    else ToolEffectStatus.UNKNOWN,
                    content=ToolJsonContent(
                        FrozenJsonObject({"path": plan.path, "rollback_state": state})
                    )
                    if state == "committed"
                    else None,
                    error=ToolErrorInfo(
                        "patch_rollback_failed", "Patch target state is unknown."
                    )
                    if state == "unknown"
                    else None,
                    certainty=EffectCertainty.CERTAIN
                    if state == "committed"
                    else EffectCertainty.UNKNOWN,
                )
            )
        return tuple(effects)


def _restore(target: Path, data: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".rollback", dir=target.parent
    )
    path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
        os.replace(path, target)
    finally:
        path.unlink(missing_ok=True)


class _PatchRollbackIncomplete(RuntimeError):
    pass


def _rollback_record(
    handler: _PatchSettlement, plans: tuple[_Plan, ...]
) -> SupplementalExecutionRecord:
    return SupplementalExecutionRecord(
        ExecutionRecordStatus.UNKNOWN,
        "patch_rollback_failed",
        FrozenJsonObject(
            {
                "patch_target_states": [
                    {
                        "path": plan.path,
                        "state": handler.states.get(plan.path, "unknown"),
                    }
                    for plan in plans
                ]
            }
        ),
    )


def _success_effect(plan: _Plan, context: ToolContext) -> ToolEffectRecord:
    return ToolEffectRecord(
        context.execution.lineage.tool_call_id or "apply_patch",  # type: ignore[union-attr]
        "apply_patch",
        ToolEffectKind.SIDE_EFFECTING,
        ToolEffectStatus.SUCCEEDED,
        content=ToolJsonContent(
            FrozenJsonObject(
                {
                    "path": plan.path,
                    "operation": plan.operation,
                    "before_digest": plan.before_digest,
                    "after_digest": plan.after_digest,
                }
            )
        ),
        certainty=EffectCertainty.CERTAIN,
    )


def _bounded_result(plans: tuple[_Plan, ...], limit: int) -> FrozenJsonObject:
    counts = {
        "added": sum(plan.operation == "added" for plan in plans),
        "modified": sum(plan.operation == "modified" for plan in plans),
        "deleted": sum(plan.operation == "deleted" for plan in plans),
    }
    full = FrozenJsonObject(
        {
            "files": [
                {"path": plan.path, "operation": plan.operation} for plan in plans
            ],
            "files_omitted": 0,
            **counts,
            "truncated": False,
        }
    )
    if len(canonical_json_dumps(full).encode()) <= limit:
        return full
    fallback = FrozenJsonObject(
        {"files": [], "files_omitted": len(plans), **counts, "truncated": True}
    )
    if len(canonical_json_dumps(fallback).encode()) > limit:
        _fail(
            "patch_result_too_large", "Bounded patch result exceeds max_result_bytes."
        )
    return fallback


def _digest(data: bytes | None) -> str | None:
    return None if data is None else f"sha256:{hashlib.sha256(data).hexdigest()}"


def _fail(code: str, message: str, cause: BaseException | None = None) -> NoReturn:
    error = ToolExecutionFailure(ToolErrorInfo(code, message))
    if cause is not None:
        raise error from cause
    raise error
