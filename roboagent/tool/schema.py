"""Schema model for RoboAgent tool metadata."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from roboagent.tool.errors import ToolRegistrationError


def _normalize_string_sequence(value: Any) -> tuple[str, ...]:
    """Normalize one string or string sequence into a tuple of tokens.

    Args:
        value: Raw value supplied to a string-sequence field.

    Returns:
        A normalized tuple of non-empty strings.

    Raises:
        ValueError: If the value is not a string or string sequence.
    """
    if value is None or value == "":
        return ()

    if isinstance(value, str):
        return tuple(dict.fromkeys(part for part in value.split() if part))

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(dict.fromkeys(str(part).strip() for part in value if str(part).strip()))

    raise ValueError("Expected a string or sequence of strings.")


class ToolSpec(BaseModel):
    """Declarative schema for one managed tool.

    This schema only models tool-management metadata. It intentionally does
    not own execution behavior or argument schemas from LangChain `BaseTool`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(description="Tool identifier. Must match the underlying BaseTool name.")
    description: str = Field(description="Human-readable summary of what the tool does.")
    group: str = Field(description="Logical group used for filtering related tools.")
    source: str = Field(description="Logical source label such as builtin, project, or mcp.")
    visible_by_default: bool = Field(default=True, description="Whether the tool is bound to the model without discovery.")
    deferred: bool = Field(default=False, description="Whether the tool should be deferred from direct binding.")
    allowed_agents: tuple[str, ...] = Field(default=(), description="Optional allowlist of agent or subagent identifiers.")

    @field_validator("name", "description", "group", "source")
    @classmethod
    def validate_non_empty_strings(cls, value: str) -> str:
        """Validate that required string fields are non-empty.

        Args:
            value: Candidate string value.

        Returns:
            The validated string.

        Raises:
            ToolRegistrationError: If the string is empty.
        """
        if not value:
            raise ToolRegistrationError("Required tool metadata fields must not be empty.")
        return value

    @field_validator("allowed_agents", mode="before")
    @classmethod
    def normalize_allowed_agents(cls, value: Any) -> tuple[str, ...]:
        """Normalize the `allowed_agents` field into a tuple.

        Args:
            value: Raw field value.

        Returns:
            A normalized tuple of agent identifiers.
        """
        return _normalize_string_sequence(value)


__all__ = ["ToolSpec"]
