"""Bounded, immutable Skill catalog public API."""

from .loader import SKILL_FILE_NAME, SkillLoader
from .manager import SkillManager, create_read_skill_tool
from .skill import (
    SkillCatalog,
    SkillConfig,
    SkillDiagnostic,
    SkillMetadata,
    SkillReadError,
    SkillSource,
)

__all__ = [
    "SKILL_FILE_NAME",
    "SkillCatalog",
    "SkillConfig",
    "SkillDiagnostic",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
    "SkillReadError",
    "SkillSource",
    "create_read_skill_tool",
]
