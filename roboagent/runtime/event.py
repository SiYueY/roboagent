"""Media-safe public event records for a single V1 run."""
from __future__ import annotations
from dataclasses import dataclass, field
from time import time
from .types import ContentSummary

@dataclass(frozen=True, slots=True)
class AgentEvent:
    run_id: str; sequence: int; type: str; turn: int | None = None
    content: tuple[ContentSummary, ...] = (); text: str | None = None
    tool_call_id: str | None = None; tool_name: str | None = None
    status: str | None = None; error_code: str | None = None; error: str | None = None
    timestamp: float = field(default_factory=time)
    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("AgentEvent requires run_id and positive sequence.")
        if not isinstance(self.type, str) or not self.type:
            raise ValueError("AgentEvent.type must be non-empty str.")
        if not isinstance(self.content, tuple) or not all(isinstance(item, ContentSummary) for item in self.content):
            raise TypeError("AgentEvent.content must be ContentSummary tuple.")
        for value in (self.text, self.tool_call_id, self.tool_name, self.status, self.error_code, self.error):
            if value is not None and not isinstance(value, str): raise TypeError("AgentEvent textual fields must be str | None.")
