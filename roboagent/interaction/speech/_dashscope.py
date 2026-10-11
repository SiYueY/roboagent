"""Small compatibility helpers shared by DashScope speech adapters."""

from __future__ import annotations

from typing import Any


def get_field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from SDK objects that may be mappings or attributes."""
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )
