"""Canonical Model and ModelProvider public API."""

from .client import (
    FinishReason,
    Model,
    ModelCapabilities,
    ModelEvent,
    ModelProvider,
    ModelResponse,
    ModelSettings,
    OpenAICompatibleModel,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageUpdated,
    collect_model_stream,
)
from .errors import ModelCapabilityError, ModelError, ModelProtocolError, ModelProviderError
from .factory import ConfiguredModelProvider, create_model
from .registry import ModelRegistry

__all__ = [
    "ConfiguredModelProvider",
    "FinishReason",
    "Model",
    "ModelCapabilities",
    "ModelCapabilityError",
    "ModelError",
    "ModelEvent",
    "ModelProtocolError",
    "ModelProvider",
    "ModelProviderError",
    "ModelRegistry",
    "ModelResponse",
    "ModelSettings",
    "OpenAICompatibleModel",
    "ResponseCompleted",
    "ResponseStarted",
    "TextDelta",
    "ToolCallArgumentsDelta",
    "ToolCallCompleted",
    "ToolCallStarted",
    "Usage",
    "UsageUpdated",
    "collect_model_stream",
    "create_model",
]
