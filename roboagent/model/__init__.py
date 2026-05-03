"""Public API for the RoboAgent model subsystem."""

from __future__ import annotations

from typing import Any

from roboagent.model.registry import ModelRegistry


def create_chat_model(name: str | None = None, *, registry: ModelRegistry, **kwargs: Any):
    """Create one chat model instance from configured model entries.

    Args:
        name: Optional model name. Uses registry default resolution when omitted.
        registry: Explicit model registry used for model resolution.
        **kwargs: Runtime keyword overrides merged on top of static params.

    Returns:
        A provider-backed chat model instance.
    """
    from roboagent.model.factory import create_chat_model as _create_chat_model

    return _create_chat_model(name=name, registry=registry, **kwargs)


__all__ = ["create_chat_model"]
