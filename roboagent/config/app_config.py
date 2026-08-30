"""Application-level configuration loader for RoboAgent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from roboagent.config.model_config import load_yaml_mapping, resolve_model_config_path
from roboagent.config.skill_config import SkillConfig
from roboagent.config.subagent_config import SubagentConfig
from roboagent.model.providers import ProviderModelConfig
from roboagent.model.registry import ModelRegistry
from roboagent.skill import SkillManager


class AppConfig(BaseModel):
    """Validated top-level RoboAgent application configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    default_model: str | None = Field(default=None, description="Default configured model name.")
    models: list[ProviderModelConfig] = Field(default_factory=list, description="Provider-backed chat models.")
    skills: SkillConfig = Field(default_factory=SkillConfig, description="Skill subsystem configuration.")
    subagents: list[SubagentConfig] = Field(default_factory=list, description="Configured sub-agent profiles.")

    @model_validator(mode="after")
    def validate_uniqueness(self) -> AppConfig:
        model_names = [model.name for model in self.models]
        duplicate_models = sorted(name for name, count in Counter(model_names).items() if count > 1)
        if duplicate_models:
            raise ValueError(f"Duplicate model names in models: {', '.join(duplicate_models)}")

        if self.default_model is not None and self.default_model not in set(model_names):
            raise ValueError(f"default_model '{self.default_model}' is not present in models[].name")

        subagent_ids = [subagent.id for subagent in self.subagents]
        duplicate_subagents = sorted(name for name, count in Counter(subagent_ids).items() if count > 1)
        if duplicate_subagents:
            raise ValueError(f"Duplicate subagent ids: {', '.join(duplicate_subagents)}")

        return self

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppConfig:
        """Build application configuration from a mapping."""
        return cls.model_validate(dict(data))

    @classmethod
    def from_yaml(cls, path: str | Path) -> AppConfig:
        """Load application configuration from a YAML file."""
        return cls.from_dict(load_yaml_mapping(path))

    def to_model_registry(self) -> ModelRegistry:
        """Create a model registry from configured model entries."""
        return ModelRegistry(models=self.models, default_model=self.default_model)

    def create_skill_manager(self) -> SkillManager:
        """Create a skill manager using configured skill sources."""
        return SkillManager(sources=self.skills.sources)


def load_app_config(path: str | Path | None = None) -> AppConfig:
    """Load top-level RoboAgent configuration from path, env, or default file."""
    return AppConfig.from_yaml(resolve_model_config_path(path))


__all__ = ["AppConfig", "load_app_config"]
