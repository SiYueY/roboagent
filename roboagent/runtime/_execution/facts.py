"""Execution attribution, evidence, and retry facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from roboagent.message import FrozenJsonObject, freeze_json_object
from roboagent.model import Usage

if TYPE_CHECKING:
    from roboagent.tool import ToolEffectRecord


@dataclass(frozen=True, slots=True, order=True)
class EffectIdentity:
    scope_id: str
    sequence: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope_id, str)
            or not self.scope_id
            or type(self.sequence) is not int
            or self.sequence < 0
        ):
            raise ValueError("Invalid EffectIdentity.")


@dataclass(frozen=True, slots=True)
class ContributionId:
    scope_id: str
    sequence: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope_id, str)
            or not self.scope_id
            or type(self.sequence) is not int
            or self.sequence < 0
        ):
            raise ValueError("Invalid ContributionId.")


class UsageKnowledge(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UsageContribution:
    state: UsageKnowledge
    usage: Usage | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, UsageKnowledge):
            raise TypeError("UsageContribution.state must be UsageKnowledge.")
        if self.state is UsageKnowledge.KNOWN and not isinstance(self.usage, Usage):
            raise ValueError("KNOWN usage requires a Usage value.")
        if self.state is UsageKnowledge.UNKNOWN and self.usage is not None:
            raise ValueError("UNKNOWN usage cannot contain a Usage value.")


class ExecutionRecordStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ExecutionRecordType(str, Enum):
    TOOL = "tool"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class SupplementalExecutionRecord:
    status: ExecutionRecordStatus
    error_code: str | None = None
    evidence: FrozenJsonObject | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionRecordStatus):
            raise TypeError("SupplementalExecutionRecord.status must be canonical.")
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code
        ):
            raise ValueError("error_code must be non-empty or None.")
        if self.evidence is not None:
            object.__setattr__(self, "evidence", freeze_json_object(self.evidence))


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    sequence: int
    record_type: ExecutionRecordType
    root_run_id: str
    execution_run_id: str
    scope_id: str
    tool_call_id: str | None
    tool_name: str | None
    arguments_digest: str | None
    arguments_preview: FrozenJsonObject | None
    status: ExecutionRecordStatus
    error_code: str | None
    evidence: FrozenJsonObject | None

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or self.sequence < 0
            or not isinstance(self.record_type, ExecutionRecordType)
        ):
            raise ValueError("Invalid ExecutionRecord identity.")
        if not all(
            isinstance(value, str) and value
            for value in (self.root_run_id, self.execution_run_id, self.scope_id)
        ):
            raise ValueError("ExecutionRecord requires execution identities.")
        if not isinstance(self.status, ExecutionRecordStatus):
            raise TypeError("ExecutionRecord.status must be canonical.")
        if self.arguments_preview is not None:
            object.__setattr__(
                self, "arguments_preview", freeze_json_object(self.arguments_preview)
            )
        if self.evidence is not None:
            object.__setattr__(self, "evidence", freeze_json_object(self.evidence))


@dataclass(frozen=True, slots=True)
class CleanupError:
    scope_id: str
    resource_type: str
    code: str
    message: str
    forced: bool


class RetryBlockerCode(str, Enum):
    SETTLEMENT_UNCERTAIN = "settlement_uncertain"
    TRUSTED_EXECUTION = "trusted_execution"
    CLEANUP_UNCERTAIN = "cleanup_uncertain"


@dataclass(frozen=True, slots=True)
class RetryBlocker:
    code: RetryBlockerCode
    scope_id: str
    message: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.code, RetryBlockerCode)
            or not self.scope_id
            or not self.message
        ):
            raise ValueError("Invalid RetryBlocker.")


@dataclass(frozen=True, slots=True)
class ExecutionContribution:
    contribution_id: ContributionId
    usage: UsageContribution | None = None
    effects: tuple[ToolEffectRecord, ...] = ()
    records: tuple[SupplementalExecutionRecord, ...] = ()
    cleanup_errors: tuple[CleanupError, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "cleanup_errors", tuple(self.cleanup_errors))
        if not isinstance(self.contribution_id, ContributionId):
            raise TypeError("ExecutionContribution requires ContributionId.")
        if self.usage is not None and not isinstance(self.usage, UsageContribution):
            raise TypeError("ExecutionContribution.usage must be canonical.")
        if not all(
            isinstance(item, SupplementalExecutionRecord) for item in self.records
        ):
            raise TypeError("Only supplemental SUMMARY records may be contributed.")
        if not all(isinstance(item, CleanupError) for item in self.cleanup_errors):
            raise TypeError("cleanup_errors must be canonical.")
