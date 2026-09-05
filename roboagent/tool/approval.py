"""Immutable approval requests bound to canonical ToolCall arguments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from roboagent.message import FrozenJsonObject, canonical_json_digest, freeze_json_object
from roboagent.runtime import CancellationToken


class ApprovalDecision(Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: FrozenJsonObject
    arguments_digest: str
    reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.approval_id, self.run_id, self.session_id, self.tool_call_id, self.tool_name):
            if not isinstance(value, str) or not value:
                raise ValueError("ApprovalRequest identity fields must be non-empty.")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))
        if self.arguments_digest != canonical_json_digest(self.arguments):
            raise ValueError("ApprovalRequest arguments_digest does not match arguments.")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("ApprovalRequest.reason must be str or None.")


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    approval_id: str
    arguments_digest: str
    decision: ApprovalDecision

    def __post_init__(self) -> None:
        if not self.approval_id or not self.arguments_digest or not isinstance(self.decision, ApprovalDecision):
            raise ValueError("Invalid ApprovalResponse.")


class ApprovalProvider(Protocol):
    async def request(self, request: ApprovalRequest, cancellation: CancellationToken) -> ApprovalResponse: ...


@dataclass(frozen=True, slots=True)
class ApprovalSettings:
    timeout: float | None = None

    def __post_init__(self) -> None:
        if self.timeout is not None and (
            isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or self.timeout <= 0
        ):
            raise ValueError("Approval timeout must be positive or None.")
