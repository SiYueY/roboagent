"""Tongyi provider configuration and model factory."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, field_validator

from roboagent.model.errors import ModelDependencyError
from roboagent.model.providers.base import BaseModelConfig, merge_model_settings


class TongyiParams(BaseModel):
    """Provider-specific runtime parameters for Tongyi-backed models."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    model: str = Field(description="Qwen model identifier, for example `qwen-max`.")
    api_key: str | None = Field(
        default=None,
        alias="dashscope_api_key",
        description="DashScope API key. Falls back to `DASHSCOPE_API_KEY` when omitted.",
    )
    temperature: float | None = Field(default=None, description="Sampling temperature override.")
    top_p: float | None = Field(default=None, description="Nucleus sampling probability mass.")
    max_retries: int | None = Field(default=None, description="Maximum request retry attempts.")
    streaming: bool | None = Field(default=None, description="Whether to stream response chunks.")
    request_timeout: float | None = Field(default=None, description="Client request timeout in seconds.")
    model_kwargs: dict[str, Any] = Field(default_factory=dict, description="Extra keyword arguments forwarded to the model.")

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        """Validate that a non-empty model identifier is supplied.

        Args:
            value: Candidate model identifier.

        Returns:
            The validated model identifier.

        Raises:
            ValueError: If the value is empty.
        """
        if not value:
            raise ValueError("Tongyi params.model must not be empty.")
        return value


class TongyiModelConfig(BaseModelConfig):
    """Model configuration entry specialized for Tongyi."""

    provider: Literal["tongyi"] = "tongyi"
    params: TongyiParams = Field(description="Tongyi runtime parameter set.")


def create_tongyi_chat_model(config: TongyiModelConfig, **overrides: Any) -> BaseChatModel:
    """Create a LangChain `ChatTongyi` model from validated configuration.

    Args:
        config: Validated Tongyi model configuration entry.
        **overrides: Runtime keyword overrides merged on top of static params.

    Returns:
        A configured Tongyi chat model instance.

    Raises:
        ModelDependencyError: If `langchain-community` or `dashscope` is missing.
    """
    try:
        from langchain_community.chat_models.tongyi import ChatTongyi
    except ImportError as exc:
        raise ModelDependencyError(
            "Missing dependency for Tongyi provider. Install with `uv add langchain-community dashscope`."
        ) from exc

    base_settings = config.params.model_dump(exclude_none=True)
    settings = merge_model_settings(base_settings, overrides)
    return ChatTongyi(**settings)


__all__ = ["TongyiModelConfig", "TongyiParams", "create_tongyi_chat_model"]
