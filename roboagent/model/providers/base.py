"""Shared types and helpers for model provider integrations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseModelConfig(BaseModel):
    """Provider-agnostic metadata for one configured model.

    This base configuration intentionally excludes provider-specific runtime
    parameters such as temperature or API keys. Those fields belong to each
    provider's dedicated `params` model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(description="Stable internal identifier used for runtime model lookup.")
    display_name: str | None = Field(default=None, description="Human-readable model label for UI and logs.")
    provider: str = Field(description="Provider key used to resolve the concrete factory implementation.")
    enabled: bool = Field(default=True, description="Whether the model can be selected at runtime.")

    @field_validator("name", "provider")
    @classmethod
    def validate_required_non_empty(cls, value: str) -> str:
        """Validate that key identifier fields are non-empty strings.

        Args:
            value: Field value under validation.

        Returns:
            The validated value.

        Raises:
            ValueError: If the value is empty.
        """
        if not value:
            raise ValueError("name and provider must not be empty.")
        return value


def deep_merge_dicts(base: Mapping[str, Any] | None, override: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recursively merge two dictionary-like objects.

    Args:
        base: Base mapping used as merge starting point.
        override: Mapping whose values should override or extend base values.

    Returns:
        A new dictionary containing the merged values.
    """
    merged = dict(base or {})
    if not override:
        return merged

    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_model_settings(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Merge provider settings with runtime overrides.

    Args:
        base: Provider settings derived from static configuration.
        overrides: Per-call override values.

    Returns:
        A merged dictionary where runtime overrides take precedence.
    """
    deep_merge_keys = {"model_kwargs", "extra_body", "default_headers", "default_query"}
    merged = dict(base)

    for key, value in overrides.items():
        if key in deep_merge_keys and isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value

    return merged


__all__ = ["BaseModelConfig", "deep_merge_dicts", "merge_model_settings"]
