"""Configuration models for the RoboAgent skill subsystem."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_string_sequence(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(dict.fromkeys(part for part in value.split() if part))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(dict.fromkeys(str(part).strip() for part in value if str(part).strip()))
    raise ValueError("Expected a string or sequence of strings.")


def _normalize_path_sequence(value: Any) -> tuple[Path, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (str, Path)):
        return (Path(value).expanduser(),)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(Path(item).expanduser() for item in value)
    raise ValueError("Expected a path or sequence of paths.")


class SkillConfig(BaseModel):
    """Application configuration for skill discovery and permissions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sources: tuple[Path, ...] = Field(default=(), description="Directories scanned for SKILL.md packages.")
    enabled_skills: tuple[str, ...] = Field(default=(), description="Optional allowlist of skill names.")
    disabled_skills: tuple[str, ...] = Field(default=(), description="Skill names disabled after loading.")
    allowed_permissions: tuple[str, ...] = Field(default=(), description="Permission identifiers allowed at execution time.")
    require_permissions: bool = Field(default=True, description="Whether executable skills must satisfy permission checks.")
    loading_policy: Literal["skip-invalid", "strict"] = Field(
        default="skip-invalid",
        description="How invalid skill files should be handled during loading.",
    )

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> tuple[Path, ...]:
        return _normalize_path_sequence(value)

    @field_validator("enabled_skills", "disabled_skills", "allowed_permissions", mode="before")
    @classmethod
    def normalize_string_sequences(cls, value: Any) -> tuple[str, ...]:
        return _normalize_string_sequence(value)

    @model_validator(mode="after")
    def validate_skill_toggles(self) -> SkillConfig:
        overlap = set(self.enabled_skills) & set(self.disabled_skills)
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"Skills cannot be both enabled and disabled: {names}")
        return self


__all__ = ["SkillConfig"]
