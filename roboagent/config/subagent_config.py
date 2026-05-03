"""Configuration models for RoboAgent sub-agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_string_sequence(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(dict.fromkeys(part for part in value.split() if part))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(dict.fromkeys(str(part).strip() for part in value if str(part).strip()))
    raise ValueError("Expected a string or sequence of strings.")


class SubagentConfig(BaseModel):
    """Configuration for one named sub-agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(description="Stable sub-agent identifier.")
    role: str | None = Field(default=None, description="Human-readable role or operating mode.")
    allowed_tools: tuple[str, ...] = Field(default=(), description="Tool names this sub-agent may access.")
    allowed_skills: tuple[str, ...] = Field(default=(), description="Skill names this sub-agent may activate.")
    enabled: bool = Field(default=True, description="Whether this sub-agent can be selected.")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value:
            raise ValueError("subagent id must not be empty.")
        return value

    @field_validator("allowed_tools", "allowed_skills", mode="before")
    @classmethod
    def normalize_allowed_lists(cls, value: Any) -> tuple[str, ...]:
        return _normalize_string_sequence(value)


__all__ = ["SubagentConfig"]
