"""Runtime feature flags for RoboAgent assembly."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeFeatures:
    """Declarative runtime feature flags.

    Only `tool_resolution` and `skill_context` affect v1 runtime assembly.
    The remaining flags are explicit placeholders for later runtime subsystems.
    """

    tool_resolution: bool = True
    skill_context: bool = True
    guardrails: bool = False
    run_journal: bool = False
    subagent: bool = False
    sandbox: bool = False


__all__ = ["RuntimeFeatures"]
