"""Deterministic V1.3 integration-evaluation contracts and claim verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from roboagent.message import thaw_json
from roboagent.runtime import ExecutionRecordStatus, RunStatus


class EvaluationScenario(str, Enum):
    REPOSITORY_UNDERSTANDING = "repository_understanding"
    BUG_FIX = "bug_fix"
    FEATURE_CHANGE = "feature_change"
    LONG_CONTEXT = "long_context"
    FAILURE_RECOVERY = "failure_recovery"
    STEERING = "steering"
    CANCELLATION = "cancellation"
    CLAIM_VERIFICATION = "claim_verification"


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    result: object
    transcript: tuple[object, ...] = ()
    compaction_count: int = 0
    worker_alive: bool = False
    active_run: bool = False
    protected_path: str | None = None
    steer_record_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    scenario: EvaluationScenario
    passed: bool
    reasons: tuple[str, ...] = ()


def evaluate_scenario(
    scenario: EvaluationScenario, evidence: EvaluationEvidence
) -> EvaluationOutcome:
    result = evidence.result
    records = tuple(getattr(result, "execution_records", ()))
    names = [record.tool_name for record in records]
    statuses = [record.status for record in records]
    error_codes = {record.error_code for record in records if record.error_code}
    reasons: list[str] = []
    if scenario is EvaluationScenario.REPOSITORY_UNDERSTANDING:
        if not {"find_files", "search_files", "read_file"} <= set(names):
            reasons.append("missing find/search/read evidence")
    elif scenario is EvaluationScenario.BUG_FIX:
        shell = [record for record in records if record.tool_name == "shell"]
        exit_codes = _shell_exit_codes(evidence.transcript)
        if not exit_codes or exit_codes[0] == 0:
            reasons.append("missing initial failing test")
        if not {"read_file", "apply_patch", "shell"} <= set(names):
            reasons.append("missing inspect/patch/retest sequence")
        if not shell or not exit_codes or exit_codes[-1] != 0:
            reasons.append("final test did not pass")
    elif scenario is EvaluationScenario.FEATURE_CHANGE:
        if not {"read_file", "apply_patch", "shell"} <= set(names):
            reasons.append("missing understand/change/test evidence")
    elif scenario is EvaluationScenario.LONG_CONTEXT:
        if evidence.compaction_count <= 0:
            reasons.append("no context compaction")
    elif scenario is EvaluationScenario.FAILURE_RECOVERY:
        expected = {"invalid_arguments", "patch_conflict", "file_not_found"}
        if not expected & error_codes or not any(
            status is ExecutionRecordStatus.SUCCEEDED for status in statuses
        ):
            reasons.append("failure was not observed and recovered")
    elif scenario is EvaluationScenario.STEERING:
        if evidence.protected_path is None or evidence.steer_record_sequence is None:
            reasons.append("missing steering boundary")
        else:
            for record in records:
                preview = (
                    thaw_json(record.arguments_preview)
                    if record.arguments_preview is not None
                    else {}
                )
                if (
                    record.sequence > evidence.steer_record_sequence
                    and isinstance(preview, dict)
                    and preview.get("path") == evidence.protected_path
                    and record.status is ExecutionRecordStatus.SUCCEEDED
                ):
                    reasons.append("protected module changed after steer")
                    break
    elif scenario is EvaluationScenario.CANCELLATION:
        if getattr(result, "status", None) is not RunStatus.CANCELLED:
            reasons.append("run was not cancelled")
        if evidence.worker_alive or evidence.active_run:
            reasons.append("execution resources remain active")
    elif scenario is EvaluationScenario.CLAIM_VERIFICATION:
        if not getattr(result, "execution_records_complete", False):
            reasons.append("execution records are incomplete")
        if not records:
            reasons.append("claims have no execution evidence")
    return EvaluationOutcome(scenario, not reasons, tuple(reasons))


def verify_claim(result: object, *, tool_name: str, succeeded: bool = True) -> bool:
    """Return true only when complete execution records support a tool claim."""
    if not getattr(result, "execution_records_complete", False):
        return False
    expected = (
        ExecutionRecordStatus.SUCCEEDED if succeeded else ExecutionRecordStatus.FAILED
    )
    return any(
        record.tool_name == tool_name and record.status is expected
        for record in getattr(result, "execution_records", ())
    )


def _shell_exit_codes(transcript: tuple[object, ...]) -> list[int]:
    from roboagent.message import JsonContent, ToolResultMessage

    result: list[int] = []
    for message in transcript:
        if (
            not isinstance(message, ToolResultMessage)
            or message.tool_name != "execute_python"
        ):
            continue
        for content in message.content:
            if not isinstance(content, JsonContent):
                continue
            envelope = thaw_json(content.value)
            # Nested shell values appear in stdout only when model code prints
            # them; integration fixtures deliberately print stable exit tags.
            observation = (
                envelope.get("observation", "") if isinstance(envelope, dict) else ""
            )
            for line in str(observation).splitlines():
                if line.startswith("SHELL_EXIT="):
                    try:
                        result.append(int(line.partition("=")[2]))
                    except ValueError:
                        pass
    return result
