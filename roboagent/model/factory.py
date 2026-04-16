"""Factory helpers for provider-backed chat model instantiation."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from roboagent.config.model_config import get_model_registry
from roboagent.model.errors import ModelProviderError
from roboagent.model.providers import (
    DeepSeekModelConfig,
    OpenAIModelConfig,
    TongyiModelConfig,
    create_deepseek_chat_model,
    create_openai_chat_model,
    create_tongyi_chat_model,
)
from roboagent.model.registry import ModelRegistry


def create_chat_model(name: str | None = None, **kwargs: Any) -> BaseChatModel:
    """Create one chat model from configured provider model entries.

    Args:
        name: Optional configured model name. Uses default-model resolution when
            omitted.
        **kwargs: Runtime keyword overrides merged on top of static params.

    Returns:
        A provider-specific `BaseChatModel` implementation.

    Raises:
        ModelProviderError: If the provider type is unsupported.
    """
    registry: ModelRegistry = get_model_registry()
    model_config = registry.resolve(name)

    if isinstance(model_config, OpenAIModelConfig):
        return create_openai_chat_model(model_config, **kwargs)
    if isinstance(model_config, DeepSeekModelConfig):
        return create_deepseek_chat_model(model_config, **kwargs)
    if isinstance(model_config, TongyiModelConfig):
        return create_tongyi_chat_model(model_config, **kwargs)

    raise ModelProviderError(
        f"Unsupported model provider '{model_config.provider}' for model '{model_config.name}'."
    )


__all__ = ["create_chat_model"]
