from __future__ import annotations


class SkillError(Exception):
    """Base exception for skill subsystem failures."""


class SkillRegistryError(SkillError):
    """Base exception for skill registry failures."""


class SkillManagerError(SkillError):
    """Base exception for skill manager failures."""


class SkillLoadError(SkillError):
    """Raised when a skill file cannot be parsed into a valid runtime skill."""


class SkillExecutionError(SkillError):
    """Base exception for skill execution failures."""


class SkillEntrypointError(SkillExecutionError):
    """Raised when a skill entrypoint or schema reference cannot be resolved."""


class SkillPermissionError(SkillExecutionError):
    """Raised when a skill lacks required execution permissions."""


class SkillValidationError(SkillExecutionError):
    """Raised when skill input or output validation fails."""


class DuplicateSkillError(SkillRegistryError):
    """Raised when attempting to register an already-existing skill without replacement."""


class SkillNotFoundError(SkillRegistryError):
    """Raised when a requested skill does not exist in the registry."""


__all__ = [
    "DuplicateSkillError",
    "SkillError",
    "SkillEntrypointError",
    "SkillExecutionError",
    "SkillLoadError",
    "SkillManagerError",
    "SkillNotFoundError",
    "SkillPermissionError",
    "SkillRegistryError",
    "SkillValidationError",
]
