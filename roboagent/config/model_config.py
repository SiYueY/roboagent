"""Configuration models for provider-backed chat model settings."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from roboagent.model.errors import ModelConfigError
from roboagent.model.providers import ProviderModelConfig
from roboagent.model.registry import ModelRegistry

_CONFIG_PATH_ENV_VAR = "ROBOAGENT_CONFIG_PATH"
_DEFAULT_CONFIG_PATH = Path("config.yaml")

_registry_cache: ModelRegistry | None = None
_registry_cache_path: Path | None = None
_registry_cache_mtime: float | None = None


class ModelsAppConfig(BaseModel):
    """Application-level container for chat model configurations.

    Attributes:
        default_model: Optional default model name used when callers do not
            provide an explicit model name.
        models: Registered provider-backed model entries.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    default_model: str | None = Field(default=None, description="Default model name used for implicit model resolution.")
    models: list[ProviderModelConfig] = Field(default_factory=list, description="Available provider-backed model configurations.")

    @model_validator(mode="after")
    def validate_model_names(self) -> ModelsAppConfig:
        """Validate uniqueness of names and existence of the default model.

        Returns:
            The validated configuration model.

        Raises:
            ValueError: If duplicate model names are present or the default
                model does not exist in `models`.
        """
        names = [model.name for model in self.models]
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            duplicate_names = ", ".join(duplicates)
            raise ValueError(f"Duplicate model names in models: {duplicate_names}")

        if self.default_model is not None and self.default_model not in set(names):
            raise ValueError(f"default_model '{self.default_model}' is not present in models[].name")

        return self

    def get_model_config(self, name: str) -> ProviderModelConfig | None:
        """Return one model configuration by name if present.

        Args:
            name: Model name.

        Returns:
            Matching provider model configuration, or `None` if absent.
        """
        for model in self.models:
            if model.name == name:
                return model
        return None

    def require_model_config(self, name: str) -> ProviderModelConfig:
        """Return one model configuration by name or raise.

        Args:
            name: Model name.

        Returns:
            Matching provider model configuration.

        Raises:
            ModelConfigError: If no model with `name` exists.
        """
        model = self.get_model_config(name)
        if model is None:
            raise ModelConfigError(f"Model '{name}' is not present in app configuration.")
        return model

    def to_registry(self):
        """Create a `ModelRegistry` populated from this configuration.

        Returns:
            A registry containing all configured model entries.
        """
        return ModelRegistry(models=self.models, default_model=self.default_model)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelsAppConfig:
        """Build model configuration from an application configuration mapping.

        Args:
            data: Mapping that may include many application-level sections.

        Returns:
            Parsed and validated model configuration container.
        """
        payload = {
            "default_model": data.get("default_model"),
            "models": data.get("models", []),
        }
        return cls.model_validate(payload)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ModelsAppConfig:
        """Load model configuration from a YAML file.

        Args:
            path: YAML file path containing application configuration.

        Returns:
            Parsed and validated model configuration container.

        Raises:
            ModelConfigError: If the file content is not a YAML mapping.
        """
        resolved_path = Path(path)
        with resolved_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, Mapping):
            raise ModelConfigError(f"Model config file must be a mapping: {resolved_path}")
        return cls.from_dict(raw)


def resolve_model_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the model config path from argument, env, or default location.

    Args:
        config_path: Optional explicit model config path.

    Returns:
        Resolved absolute path to the model configuration file.
    """
    if config_path is not None:
        return Path(config_path).expanduser().resolve()

    configured_path = os.getenv(_CONFIG_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    return _DEFAULT_CONFIG_PATH.resolve()


def _read_mtime(path: Path) -> float:
    """Read one file's mtime and normalize missing files to sentinel value.

    Args:
        path: File path to inspect.

    Returns:
        File modification time, or `-1.0` if file does not exist.
    """
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return -1.0


def reload_model_registry(config_path: str | Path | None = None) -> ModelRegistry:
    """Reload model configuration and rebuild the in-memory model registry.

    Args:
        config_path: Optional explicit model config path.

    Returns:
        Newly loaded model registry instance.
    """
    global _registry_cache, _registry_cache_path, _registry_cache_mtime

    resolved_path = resolve_model_config_path(config_path)
    app_config = ModelsAppConfig.from_yaml(resolved_path)

    _registry_cache = app_config.to_registry()
    _registry_cache_path = resolved_path
    _registry_cache_mtime = _read_mtime(resolved_path)
    return _registry_cache


def get_model_registry(config_path: str | Path | None = None) -> ModelRegistry:
    """Get the model registry, lazily loading and refreshing when needed.

    Args:
        config_path: Optional explicit model config path.

    Returns:
        Cached or newly loaded model registry instance.
    """
    global _registry_cache, _registry_cache_path, _registry_cache_mtime

    resolved_path = resolve_model_config_path(config_path)
    current_mtime = _read_mtime(resolved_path)

    should_reload = (
        _registry_cache is None
        or _registry_cache_path != resolved_path
        or _registry_cache_mtime != current_mtime
    )
    if should_reload:
        return reload_model_registry(resolved_path)

    return _registry_cache


def reset_model_registry() -> None:
    """Clear the cached model registry and path metadata."""
    global _registry_cache, _registry_cache_path, _registry_cache_mtime

    _registry_cache = None
    _registry_cache_path = None
    _registry_cache_mtime = None


__all__ = [
    "ModelsAppConfig",
    "get_model_registry",
    "reload_model_registry",
    "reset_model_registry",
    "resolve_model_config_path",
]
