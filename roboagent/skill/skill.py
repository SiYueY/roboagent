"""Immutable Skill metadata, diagnostics, and catalog revisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4


class SkillSource(Enum):
    PROJECT = "project"
    USER = "user"


@dataclass(frozen=True, slots=True)
class SkillConfig:
    max_description_chars: int = 512
    max_body_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (self.max_description_chars, self.max_body_bytes)):
            raise ValueError("Skill limits must be positive.")


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    path: Path
    source: SkillSource

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str) or not self.description:
            raise ValueError("Skill metadata requires name and description.")
        if not isinstance(self.path, Path) or not isinstance(self.source, SkillSource):
            raise TypeError("Skill metadata requires canonical path and source.")


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    code: str
    name: str | None = None
    source: SkillSource | None = None
    paths: tuple[Path, ...] = ()
    selected_path: Path | None = None
    ignored_path: Path | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(self.paths))


@dataclass(frozen=True, slots=True)
class SkillEntry:
    metadata: SkillMetadata
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, SkillMetadata) or not isinstance(self.body, str):
            raise TypeError("SkillEntry requires canonical metadata and UTF-8 text.")


class SkillReadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    entries: tuple[SkillEntry, ...] = ()
    revision: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if not all(isinstance(entry, SkillEntry) for entry in self.entries):
            raise TypeError("SkillCatalog entries must be SkillEntry values.")
        if not self.revision:
            object.__setattr__(self, "revision", uuid4().hex)
        names = [entry.metadata.name for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("SkillCatalog names must be unique.")

    @property
    def metadata(self) -> tuple[SkillMetadata, ...]:
        return tuple(entry.metadata for entry in self.entries)

    def get(self, name: str) -> SkillEntry | None:
        return next((entry for entry in self.entries if entry.metadata.name == name), None)

    def load(self, name: str) -> str:
        entry = self.get(name)
        if entry is None:
            raise SkillReadError("unknown_skill", f"Unknown skill: {name}")
        return entry.body
