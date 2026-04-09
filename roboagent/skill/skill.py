"""Runtime skill value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Skill:
    """Immutable runtime representation of a loaded skill.

    This object is built by `SkillLoader` after schema validation and is the
    primary value stored by the registry and manager layers.
    """

    # Stable skill identifier shared across schema, registry, and selection.
    name: str
    # Human-readable summary used for display and lexical matching.
    description: str
    # Logical source name, typically derived from the source root directory.
    source: str
    # Directory containing the loaded `SKILL.md` file.
    source_dir: Path
    # Optional license identifier copied from the declarative spec.
    license: str | None = None
    # Optional runtime compatibility constraint copied from the declarative spec.
    compatibility: str | None = None
    # Semantic version of the skill definition.
    version: str = "0.1.0"
    # Markdown body used as the prompt template.
    body: str = ""
    # Keywords used to improve lexical selection.
    trigger_keywords: tuple[str, ...] = ()
    # Free-form tags used for organization and matching.
    tags: tuple[str, ...] = ()
    # Tool identifiers the skill may invoke.
    allowed_tools: tuple[str, ...] = ()
    # Permission identifiers required before the skill can run.
    required_permissions: tuple[str, ...] = ()
    # Optional Python callable reference in `module.submodule:function` form.
    entrypoint: str | None = None
    # Project-specific metadata preserved after schema normalization.
    metadata: dict[str, str] = field(default_factory=dict)
    # Absolute path to the originating `SKILL.md` file when available.
    skill_file: Path | None = None
    # Runtime toggle used by the registry and manager.
    enabled: bool = True

    @property
    def prompt_template(self) -> str:
        """Return the markdown body used as the skill prompt template."""
        return self.body

    @property
    def identity(self) -> str:
        """Return the stable `<name>@<version>` identifier for the skill."""
        return f"{self.name}@{self.version}"

    @property
    def is_executable(self) -> bool:
        """Return whether the skill declares a Python entrypoint."""
        return self.entrypoint is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the runtime skill into a Python dictionary.

        Returns:
            A dictionary containing both declarative fields and runtime
            bookkeeping fields such as `source`, `skill_file`, and `enabled`.
        """
        return {
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "version": self.version,
            "body": self.body,
            "trigger_keywords": self.trigger_keywords,
            "tags": self.tags,
            "allowed_tools": self.allowed_tools,
            "required_permissions": self.required_permissions,
            "entrypoint": self.entrypoint,
            "metadata": dict(self.metadata),
            "source": self.source,
            "source_dir": str(self.source_dir),
            "skill_file": str(self.skill_file) if self.skill_file is not None else None,
            "enabled": self.enabled,
        }

    def __str__(self) -> str:
        return self.identity


__all__ = ["Skill"]
