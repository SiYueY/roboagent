"""Shared normalizers for configuration models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def normalize_string_sequence(value: Any) -> tuple[str, ...]:
    """Normalize whitespace-delimited or sequence configuration values."""
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(dict.fromkeys(part for part in value.split() if part))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            dict.fromkeys(str(part).strip() for part in value if str(part).strip())
        )
    raise ValueError("Expected a string or sequence of strings.")
