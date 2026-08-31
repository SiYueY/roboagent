"""Application-supplied speech transports."""
from .base import SpeechTransport

__all__ = ["SpeechTransport"]
from .base import SpeechTransport
from .local import LocalSpeechTransport

__all__ = ["LocalSpeechTransport", "SpeechTransport"]
