"""TTS service contracts and provider adapters."""
from .base import TTS
from .dashscope import DashScopeTTS

__all__ = ["TTS", "DashScopeTTS"]
