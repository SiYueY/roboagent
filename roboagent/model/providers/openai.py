"""OpenAI provider configuration and model factory."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, field_validator

from roboagent.model.errors import ModelDependencyError
from roboagent.model.providers.base import BaseModelConfig, merge_model_settings


class OpenAIParams(BaseModel):
    """Provider-specific runtime parameters for OpenAI chat models."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: str = Field(description="OpenAI model identifier, for example `gpt-4o-mini`.")
    api_key: str | None = Field(default=None, description="OpenAI API key. Falls back to `OPENAI_API_KEY` when omitted.")
    base_url: str | None = Field(default=None, description="Optional OpenAI-compatible endpoint base URL.")
    organization: str | None = Field(default=None, description="Optional OpenAI organization identifier.")
    temperature: float | None = Field(default=None, description="Sampling temperature override.")
    max_tokens: int | None = Field(default=None, description="Maximum generated token count.")
    max_retries: int | None = Field(default=None, description="Maximum request retry attempts.")
    request_timeout: float | None = Field(default=None, description="Client request timeout in seconds.")
    streaming: bool | None = Field(default=None, description="Whether to stream response chunks.")
    top_p: float | None = Field(default=None, description="Nucleus sampling probability mass.")
    use_responses_api: bool | None = Field(default=None, description="Whether to route through the Responses API.")
    output_version: str | None = Field(default=None, description="Responses output version, for example `responses/v1`.")
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
            raise ValueError("OpenAI params.model must not be empty.")
        return value


class OpenAIModelConfig(BaseModelConfig):
    """Model configuration entry specialized for the OpenAI provider."""

    provider: Literal["openai"] = "openai"
    params: OpenAIParams = Field(description="OpenAI runtime parameter set.")


def create_openai_chat_model(config: OpenAIModelConfig, **overrides: Any) -> BaseChatModel:
    """Create a LangChain `ChatOpenAI` model from validated configuration.

    Args:
        config: Validated OpenAI model configuration entry.
        **overrides: Runtime keyword overrides merged on top of static params.

    Returns:
        A configured OpenAI chat model instance.

    Raises:
        ModelDependencyError: If `langchain-openai` is not installed.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ModelDependencyError(
            "Missing dependency for OpenAI provider. Install with `uv add langchain-openai`."
        ) from exc

    base_settings = config.params.model_dump(exclude_none=True)
    settings = merge_model_settings(base_settings, overrides)
    return ChatOpenAI(**settings)


__all__ = ["OpenAIModelConfig", "OpenAIParams", "create_openai_chat_model"]
