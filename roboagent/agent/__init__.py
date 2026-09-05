"""Canonical Agent, Session, Run, hook, and result API."""

from .agent import Agent
from .hooks import (
    HookDecision,
    ModelHookContext,
    RunEndHookContext,
    RunHook,
    RunHookContext,
    ToolHookContext,
)
from .run import Run
from .persistence import (
    CanonicalMessageCodec,
    InMemorySessionRepository,
    JsonSessionSnapshotCodec,
    LocalSessionRepository,
    SessionConflictError,
    SessionCorruptedError,
    SessionPersistenceError,
    SessionRepository,
    SessionSnapshot,
    SessionSnapshotCodec,
    SessionVersionUnsupportedError,
)
from .session import (
    InputReceipt,
    PendingInput,
    Session,
    SessionBusyError,
    SessionClosedError,
    SessionOwnershipError,
)
from .types import RunConfig, RunResult

__all__ = [
    "Agent",
    "HookDecision",
    "InputReceipt",
    "PendingInput",
    "CanonicalMessageCodec",
    "InMemorySessionRepository",
    "JsonSessionSnapshotCodec",
    "LocalSessionRepository",
    "ModelHookContext",
    "Run",
    "RunConfig",
    "RunEndHookContext",
    "RunHook",
    "RunHookContext",
    "RunResult",
    "Session",
    "SessionBusyError",
    "SessionConflictError",
    "SessionCorruptedError",
    "SessionClosedError",
    "SessionOwnershipError",
    "SessionPersistenceError",
    "SessionRepository",
    "SessionSnapshot",
    "SessionSnapshotCodec",
    "SessionVersionUnsupportedError",
    "ToolHookContext",
]
