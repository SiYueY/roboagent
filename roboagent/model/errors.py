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

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class ModelProtocolError(ModelError):
    """A provider stream cannot be normalized to the canonical protocol."""

    def __init__(self, code: str, message: str, provider: str | None = None) -> None:
        self.code = code
        self.provider = provider
        super().__init__(message)


class ModelCapabilityError(ModelError):
    """The model cannot accept or produce a requested canonical capability."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ModelDependencyError(ModelProviderError):
    """Raised when optional provider dependencies are missing."""


__all__ = [
    "DuplicateModelError",
    "ModelConfigError",
    "ModelDependencyError",
    "ModelError",
    "ModelCapabilityError",
    "ModelNotFoundError",
    "ModelProviderError",
    "ModelProtocolError",
    "ModelRegistryError",
]
