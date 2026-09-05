"""Public exports for model provider integrations."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from roboagent.model.providers.deepseek import (
    DeepSeekModelConfig,
    DeepSeekParams,
    create_deepseek_model,
)
from roboagent.model.providers.openai import (
    OpenAIModelConfig,
    OpenAIParams,
    create_openai_model,
)
from roboagent.model.providers.tongyi import (
    TongyiModelConfig,
    TongyiParams,
    create_tongyi_model,
)

ProviderModelConfig = Annotated[
    OpenAIModelConfig | DeepSeekModelConfig | TongyiModelConfig,
    Field(discriminator="provider"),
]
"""Discriminated union of all supported provider model configurations."""


__all__ = [
    "DeepSeekModelConfig",
    "DeepSeekParams",
    "OpenAIModelConfig",
    "OpenAIParams",
    "ProviderModelConfig",
    "TongyiModelConfig",
    "TongyiParams",
    "create_deepseek_model",
    "create_openai_model",
    "create_tongyi_model",
]
