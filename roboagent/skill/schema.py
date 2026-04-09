"""Schema model for RoboAgent skill definitions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_SKILL_VERSION: Final = "0.1.0"
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
_COMMA_SEPARATED_METADATA_FIELDS: Final = frozenset({"trigger-keywords", "tags"})
StringSplitMode = Literal["comma", "whitespace"]


def _dedupe_preserve_order(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _normalize_string_sequence(value: Any, *, split_on: StringSplitMode) -> tuple[str, ...]:
    if value is None or value == "":
        return ()

    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")] if split_on == "comma" else value.split()
        return _dedupe_preserve_order([part for part in parts if part])

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = [str(part).strip() for part in value]
        return _dedupe_preserve_order([part for part in parts if part])

    raise ValueError("Expected a string or sequence of strings.")


def _normalize_metadata_extension_value(metadata_key: str, value: Any) -> str:
    if metadata_key in _COMMA_SEPARATED_METADATA_FIELDS:
        return ",".join(_normalize_string_sequence(value, split_on="comma"))
    if metadata_key == "required-permissions":
        return " ".join(_normalize_string_sequence(value, split_on="whitespace"))
    return str(value).strip()


class SkillSpec(BaseModel):
    """Declarative schema for a skill definition.

    This model represents the external `SKILL.md` contract. Official Agent
    Skills frontmatter fields remain top-level, while project-specific
    extensions are normalized into `metadata`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    FRONTMATTER_FIELD_ALIASES: ClassVar[dict[str, str]] = {
        "allowed-tools": "allowed_tools",
    }
    _CUSTOM_METADATA_FIELD_ALIASES: ClassVar[dict[str, str]] = {
        "version": "version",
        "tags": "tags",
        "trigger_keywords": "trigger-keywords",
        "trigger-keywords": "trigger-keywords",
        "required_permissions": "required-permissions",
        "required-permissions": "required-permissions",
        "entrypoint": "entrypoint",
    }

    name: str = Field(description="Unique skill identifier in kebab-case.")
    description: str = Field(description="Short human-readable summary of the skill.")
    license: str | None = Field(default=None, description="Optional license identifier for the skill content.")
    compatibility: str | None = Field(
        default=None,
        description="Optional compatibility constraint describing which RoboAgent versions can use the skill.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Project-specific extension fields normalized from frontmatter.",
    )
    allowed_tools: tuple[str, ...] = Field(
        default=(),
        description="Tool identifiers that the skill is allowed to invoke.",
    )
    body: str = Field(default="", description="Markdown body content that serves as the skill prompt template.")

    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, value: Any) -> Any:
        """Normalize incoming payloads before field validation.

        Args:
            value: Raw input passed to Pydantic.

        Returns:
            A normalized mapping that moves extension fields into `metadata`.
        """
        if not isinstance(value, Mapping):
            return value

        normalized = cls._normalize_external_keys(value)
        metadata = normalized.get("metadata")
        metadata_dict: dict[str, Any] | Any
        if metadata is None:
            metadata_dict = {}
        elif isinstance(metadata, Mapping):
            metadata_dict = dict(metadata)
        else:
            metadata_dict = metadata

        if not isinstance(metadata_dict, dict):
            return normalized

        for source_key, metadata_key in cls._CUSTOM_METADATA_FIELD_ALIASES.items():
            if source_key in normalized and metadata_key not in metadata_dict:
                metadata_dict[metadata_key] = _normalize_metadata_extension_value(
                    metadata_key,
                    normalized.pop(source_key),
                )
        normalized["metadata"] = metadata_dict

        return normalized

    @model_validator(mode="after")
    def validate_extensions(self) -> SkillSpec:
        """Validate project-specific extension values stored in metadata.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If any supported metadata extension is malformed.
        """
        version = self.metadata.get("version")
        if version and not _SEMVER_PATTERN.fullmatch(version):
            raise ValueError("metadata.version must be a valid semantic version string.")

        entrypoint = self.metadata.get("entrypoint")
        if entrypoint and not _ENTRYPOINT_PATTERN.fullmatch(entrypoint):
            raise ValueError("metadata.entrypoint must use the format 'module.submodule:function'.")

        for key, split_on in (
            ("tags", "comma"),
            ("trigger-keywords", "comma"),
            ("required-permissions", "whitespace"),
        ):
            raw_value = self.metadata.get(key)
            if raw_value is not None:
                _normalize_string_sequence(raw_value, split_on=split_on)

        return self

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name is required.")
        if len(value) > 64:
            raise ValueError("name must be 64 characters or fewer.")
        if not _SKILL_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must use lowercase alphanumeric characters with single hyphens only.")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value:
            raise ValueError("description is required.")
        if len(value) > 1024:
            raise ValueError("description must be 1024 characters or fewer.")
        return value

    @field_validator("compatibility")
    @classmethod
    def validate_compatibility(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise ValueError("compatibility must not be empty when provided.")
        if len(value) > 500:
            raise ValueError("compatibility must be 500 characters or fewer.")
        return value

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_whitespace_separated_fields(cls, value: Any) -> tuple[str, ...]:
        return _normalize_string_sequence(value, split_on="whitespace")

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping.")
        return {str(key): str(item) for key, item in value.items()}

    @property
    def identity(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def is_executable(self) -> bool:
        return self.entrypoint is not None

    @property
    def prompt_template(self) -> str:
        return self.body

    @property
    def version(self) -> str:
        return self.metadata.get("version", DEFAULT_SKILL_VERSION)

    @property
    def tags(self) -> tuple[str, ...]:
        return _normalize_string_sequence(self.metadata.get("tags", ""), split_on="comma")

    @property
    def trigger_keywords(self) -> tuple[str, ...]:
        return _normalize_string_sequence(self.metadata.get("trigger-keywords", ""), split_on="comma")

    @property
    def required_permissions(self) -> tuple[str, ...]:
        return _normalize_string_sequence(self.metadata.get("required-permissions", ""), split_on="whitespace")

    @property
    def entrypoint(self) -> str | None:
        value = self.metadata.get("entrypoint")
        return value or None

    @classmethod
    def from_frontmatter(cls, data: Mapping[str, Any], body: str = "") -> SkillSpec:
        """Build a skill spec from parsed `SKILL.md` frontmatter and body.

        Args:
            data: Parsed YAML frontmatter mapping.
            body: Markdown body content after the closing frontmatter delimiter.

        Returns:
            A validated `SkillSpec` instance.
        """
        normalized = cls._normalize_external_keys(data)
        if body:
            normalized["body"] = body
        elif "body" not in normalized:
            normalized["body"] = ""
        return cls.model_validate(normalized)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SkillSpec:
        """Build a skill spec from an internal dictionary representation.

        Args:
            data: Mapping containing skill specification fields.

        Returns:
            A validated `SkillSpec` instance.
        """
        normalized = cls._normalize_external_keys(data)
        return cls.model_validate(normalized)

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """Serialize the spec into Agent Skills frontmatter shape.

        Returns:
            A dictionary suitable for writing back to `SKILL.md` frontmatter.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "allowed-tools": " ".join(self.allowed_tools),
            "metadata": dict(self.metadata),
        }
        if self.license is not None:
            payload["license"] = self.license
        if self.compatibility is not None:
            payload["compatibility"] = self.compatibility
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Serialize the spec into a Python dictionary.

        Returns:
            A dictionary using the model's internal field names.
        """
        return self.model_dump(mode="python")

    @classmethod
    def _normalize_external_keys(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        """Translate external kebab-case keys into internal field names."""
        normalized = dict(data)
        for source_key, target_key in cls.FRONTMATTER_FIELD_ALIASES.items():
            if source_key in normalized and target_key not in normalized:
                normalized[target_key] = normalized.pop(source_key)
        return normalized


__all__ = ["DEFAULT_SKILL_VERSION", "SkillSpec"]
