"""DeepSeek provider configuration and model factory."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, field_validator

from roboagent.model.errors import ModelDependencyError
from roboagent.model.providers.base import BaseModelConfig, merge_model_settings


class DeepSeekParams(BaseModel):
    """Provider-specific runtime parameters for DeepSeek chat models."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: str = Field(description="DeepSeek model identifier, for example `deepseek-chat`.")
    api_key: str | None = Field(default=None, description="DeepSeek API key. Falls back to `DEEPSEEK_API_KEY` when omitted.")
    api_base: str | None = Field(default=None, description="DeepSeek API base URL, for example `https://api.deepseek.com/v1`.")
    temperature: float | None = Field(default=None, description="Sampling temperature override.")
    max_tokens: int | None = Field(default=None, description="Maximum generated token count.")
    max_retries: int | None = Field(default=None, description="Maximum request retry attempts.")
    request_timeout: float | None = Field(default=None, description="Client request timeout in seconds.")
    streaming: bool | None = Field(default=None, description="Whether to stream response chunks.")
    top_p: float | None = Field(default=None, description="Nucleus sampling probability mass.")
    reasoning_effort: str | None = Field(default=None, description="Reasoning effort level for capable models.")
    model_kwargs: dict[str, Any] = Field(default_factory=dict, description="Extra keyword arguments forwarded to the model.")
    extra_body: dict[str, Any] | None = Field(default=None, description="Additional request body fields for OpenAI-compatible gateways.")
    default_headers: dict[str, str] | None = Field(default=None, description="Default HTTP headers attached to each request.")
    default_query: dict[str, Any] | None = Field(default=None, description="Default query parameters attached to each request.")

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
            raise ValueError("DeepSeek params.model must not be empty.")
        return value


class DeepSeekModelConfig(BaseModelConfig):
    """Model configuration entry specialized for the DeepSeek provider."""

    provider: Literal["deepseek"] = "deepseek"
    params: DeepSeekParams = Field(description="DeepSeek runtime parameter set.")


def create_deepseek_chat_model(config: DeepSeekModelConfig, **overrides: Any) -> BaseChatModel:
    """Create a LangChain `ChatDeepSeek` model from validated configuration.

    Args:
        config: Validated DeepSeek model configuration entry.
        **overrides: Runtime keyword overrides merged on top of static params.

    Returns:
        A configured DeepSeek chat model instance.

    Raises:
        ModelDependencyError: If `langchain-deepseek` is not installed.
    """
    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError as exc:
        raise ModelDependencyError(
            "Missing dependency for DeepSeek provider. Install with `uv add langchain-deepseek`."
        ) from exc

    base_settings = config.params.model_dump(exclude_none=True)
    settings = merge_model_settings(base_settings, overrides)
    return ChatDeepSeek(**settings)


__all__ = ["DeepSeekModelConfig", "DeepSeekParams", "create_deepseek_chat_model"]
