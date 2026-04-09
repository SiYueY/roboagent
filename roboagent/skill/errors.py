from __future__ import annotations


class SkillError(Exception):
    """Base exception for skill subsystem failures."""


class SkillRegistryError(SkillError):
    """Base exception for skill registry failures."""


class SkillManagerError(SkillError):
    """Base exception for skill manager failures."""


class SkillLoadError(SkillError):
    """Raised when a skill file cannot be parsed into a valid runtime skill."""


class DuplicateSkillError(SkillRegistryError):
    """Raised when attempting to register an already-existing skill without replacement."""


class SkillNotFoundError(SkillRegistryError):
    """Raised when a requested skill does not exist in the registry."""


__all__ = [
    "DuplicateSkillError",
    "SkillError",
    "SkillLoadError",
    "SkillManagerError",
    "SkillNotFoundError",
    "SkillRegistryError",
]
