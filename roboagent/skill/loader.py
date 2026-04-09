"""Skill discovery and loading utilities."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from roboagent.skill.errors import SkillLoadError
from roboagent.skill.schema import SkillSpec
from roboagent.skill.skill import Skill

logger = logging.getLogger(__name__)

SKILL_FILE_NAME: Final = "SKILL.md"
IGNORED_DIRECTORY_NAMES: Final = frozenset(
    {".git", ".hg", ".svn", ".venv", "__pycache__", "build", "dist", "node_modules"}
)


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Split markdown content into YAML frontmatter and body text."""
    normalized = content.lstrip("\ufeff")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillLoadError("Skill file is missing valid YAML frontmatter.")

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            frontmatter_raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :]).strip()
            return frontmatter_raw, body

    raise SkillLoadError("Skill file is missing valid YAML frontmatter.")


class SkillLoader:
    """Discover, validate, and build runtime skills from source directories."""

    def __init__(self, sources: Sequence[str | Path] = ()) -> None:
        self.sources = tuple(Path(source) for source in sources)

    def discover_skill_files(self, source: str | Path) -> list[Path]:
        """Recursively discover `SKILL.md` files under a source directory.

        Args:
            source: Root directory to scan.

        Returns:
            Sorted paths to discovered `SKILL.md` files.
        """
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_dir():
            return []

        skill_files: list[Path] = []
        for current_root, dir_names, file_names in os.walk(source_path):
            dir_names[:] = sorted(
                name
                for name in dir_names
                if not name.startswith(".") and name not in IGNORED_DIRECTORY_NAMES
            )
            if SKILL_FILE_NAME in file_names:
                skill_files.append(Path(current_root) / SKILL_FILE_NAME)

        return sorted(skill_files)

    def read_skill_file(self, skill_file: str | Path) -> tuple[dict[str, Any], str]:
        """Read and split a `SKILL.md` file into frontmatter and markdown body.

        Args:
            skill_file: Path to a `SKILL.md` file.

        Returns:
            A tuple of `(frontmatter, body)` where `frontmatter` is a parsed
            YAML mapping and `body` is trimmed markdown content.

        Raises:
            SkillLoadError: If the file cannot be read or parsed.
        """
        skill_path = Path(skill_file)
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillLoadError(f"Failed to read skill file: {skill_path}") from exc

        try:
            frontmatter_raw, body = _split_frontmatter(content)
        except SkillLoadError as exc:
            raise SkillLoadError(f"{exc}: {skill_path}") from exc

        try:
            frontmatter = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError as exc:
            raise SkillLoadError(f"Invalid YAML frontmatter in skill file: {skill_path}") from exc

        if not isinstance(frontmatter, Mapping):
            raise SkillLoadError(f"Skill frontmatter must be a mapping: {skill_path}")

        return dict(frontmatter), body.strip()

    def load_skill_file(self, skill_file: str | Path, *, source: str | Path | None = None) -> Skill:
        """Load a single `SKILL.md` file into a validated runtime `Skill`.

        Args:
            skill_file: Path to the skill definition file.
            source: Optional logical source root for the skill.

        Returns:
            A validated runtime `Skill`.

        Raises:
            SkillLoadError: If the file or schema is invalid.
        """
        skill_path = Path(skill_file)
        source_path = Path(source) if source is not None else skill_path.parent.parent
        frontmatter, body = self.read_skill_file(skill_path)

        try:
            spec = SkillSpec.from_frontmatter(frontmatter, body=body)
        except ValidationError as exc:
            raise SkillLoadError(f"Skill schema validation failed for {skill_path}: {exc}") from exc

        directory_name = skill_path.parent.name
        if spec.name != directory_name:
            logger.warning(
                "Skill name '%s' does not match directory name '%s' for %s; loading anyway for compatibility",
                spec.name,
                directory_name,
                skill_path,
            )

        return self._build_skill(spec, skill_path=skill_path, source_path=source_path)

    def _build_skill(self, spec: SkillSpec, *, skill_path: Path, source_path: Path) -> Skill:
        """Build a runtime skill from a validated schema object.

        Args:
            spec: Validated skill schema.
            skill_path: Absolute path to the source `SKILL.md` file.
            source_path: Root source directory that owns the skill.

        Returns:
            A runtime `Skill` object.
        """
        return Skill(
            name=spec.name,
            description=spec.description,
            source=source_path.name or str(source_path),
            source_dir=skill_path.parent,
            license=spec.license,
            compatibility=spec.compatibility,
            version=spec.version,
            body=spec.body,
            trigger_keywords=spec.trigger_keywords,
            tags=spec.tags,
            allowed_tools=spec.allowed_tools,
            required_permissions=spec.required_permissions,
            entrypoint=spec.entrypoint,
            metadata=dict(spec.metadata),
            skill_file=skill_path,
            enabled=True,
        )

    def load_source(self, source: str | Path) -> list[Skill]:
        """Load all valid skills from a single source directory.

        Args:
            source: Root directory to scan for skills.

        Returns:
            A sorted list of valid runtime skills discovered in the source.
        """
        source_path = Path(source)
        skills: list[Skill] = []

        for skill_file in self.discover_skill_files(source_path):
            try:
                skills.append(self.load_skill_file(skill_file, source=source_path))
            except SkillLoadError as exc:
                logger.warning("Skipping invalid skill file %s: %s", skill_file, exc)

        skills.sort(key=lambda skill: skill.name)
        return skills

    def load_all(self, sources: Sequence[str | Path] | None = None) -> list[Skill]:
        """Load all valid skills from configured or provided sources.

        Args:
            sources: Optional list of source directories. If omitted, uses the
                loader's configured sources.

        Returns:
            A flat list of valid runtime skills across all sources.
        """
        resolved_sources = tuple(Path(source) for source in (sources or self.sources))
        skills: list[Skill] = []
        for source in resolved_sources:
            skills.extend(self.load_source(source))
        return skills

__all__ = ["IGNORED_DIRECTORY_NAMES", "SKILL_FILE_NAME", "SkillLoader"]
