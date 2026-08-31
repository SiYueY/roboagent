"""ASR service contracts and provider adapters."""
from .base import ASR, ASRSession
from .dashscope import DashScopeASR, DashScopeASRSession

__all__ = ["ASR", "ASRSession", "DashScopeASR", "DashScopeASRSession"]
