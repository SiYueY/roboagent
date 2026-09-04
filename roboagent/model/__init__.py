"""Model package; provider configuration is imported only when requested."""

from __future__ import annotations

from typing import Any

from .registry import ModelRegistry


def create_chat_model(
    name: str | None = None,
    *,
    registry: ModelRegistry,
    **kwargs: Any,
) -> object:
    from .factory import create_chat_model as factory

    return factory(name=name, registry=registry, **kwargs)


__all__ = ["create_chat_model"]
