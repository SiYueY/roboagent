"""Exception types for the RoboAgent tool subsystem."""

from __future__ import annotations


class ToolError(Exception):
    """Base exception for tool subsystem failures."""


class ToolRegistrationError(ToolError):
    """Raised when tool registration data is invalid."""


class DuplicateToolError(ToolRegistrationError):
    """Raised when attempting to register an already-known tool."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is absent from the registry."""


__all__ = [
    "DuplicateToolError",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistrationError",
]
