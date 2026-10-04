"""Canonical Agent, Session, Run, hook, and result API."""

from .agent import Agent
from .delegation import ChildSessionContext, ChildSessionFactory
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
    MIN_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
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
    SessionNotFoundError,
    SessionOwnershipError,
)
from .types import RunConfig, RunResult

__all__ = [
    "Agent",
    "HookDecision",
    "InputReceipt",
    "PendingInput",
    "CanonicalMessageCodec",
    "ChildSessionContext",
    "ChildSessionFactory",
    "InMemorySessionRepository",
    "JsonSessionSnapshotCodec",
    "LocalSessionRepository",
    "ModelHookContext",
    "MIN_SUPPORTED_SCHEMA_VERSION",
    "Run",
    "RunConfig",
    "RunEndHookContext",
    "RunHook",
    "RunHookContext",
    "RunResult",
    "SCHEMA_VERSION",
    "Session",
    "SessionBusyError",
    "SessionConflictError",
    "SessionCorruptedError",
    "SessionClosedError",
    "SessionNotFoundError",
    "SessionOwnershipError",
    "SessionPersistenceError",
    "SessionRepository",
    "SessionSnapshot",
    "SessionSnapshotCodec",
    "SessionVersionUnsupportedError",
    "ToolHookContext",
]
