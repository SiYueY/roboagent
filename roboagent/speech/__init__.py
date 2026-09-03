"""Real-time, transport-agnostic voice runtime for RoboAgent."""
from .config import SpeechConfig
from .session import SpeechSession
from .types import AudioChunk, AudioFormat, DEFAULT_INPUT_FORMAT, DEFAULT_OUTPUT_FORMAT, Transcript

__all__ = ["AudioChunk", "AudioFormat", "DEFAULT_INPUT_FORMAT", "DEFAULT_OUTPUT_FORMAT", "SpeechConfig", "SpeechSession", "Transcript"]
"""Optional realtime speech integration, outside the V1 Kernel contract.

It may bridge text turns to an AgentSession, but PCM transport, ASR, TTS,
VAD, and AEC never enter the canonical transcript or Kernel state machine.
"""
