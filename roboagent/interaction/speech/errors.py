"""Speech runtime error hierarchy."""


class SpeechError(RuntimeError):
    """Base class for normalized speech failures."""


class SpeechConfigurationError(SpeechError):
    """Speech optional dependency or configuration is invalid."""


class ASRError(SpeechError):
    """Speech recognition service failure."""


class TTSError(SpeechError):
    """Speech synthesis service failure."""


class TransportError(SpeechError):
    """Speech transport failure."""
