"""Exception types for the RoboAgent model subsystem."""

from __future__ import annotations


class ModelError(Exception):
    """Base exception for model subsystem failures."""


class ModelConfigError(ModelError):
    """Raised when model configuration payloads are invalid."""


class ModelRegistryError(ModelError):
    """Raised when registry operations fail."""


class DuplicateModelError(ModelRegistryError):
    """Raised when attempting to register an already-known model name."""


class ModelNotFoundError(ModelRegistryError):
    """Raised when a requested model is absent from the registry."""


class ModelProviderError(ModelError):
    """Raised when provider resolution or instantiation fails."""


class ModelDependencyError(ModelProviderError):
    """Raised when optional provider dependencies are missing."""


__all__ = [
    "DuplicateModelError",
    "ModelConfigError",
    "ModelDependencyError",
    "ModelError",
    "ModelNotFoundError",
    "ModelProviderError",
    "ModelRegistryError",
]
