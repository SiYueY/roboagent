# Agent Module

`Agent` is immutable capability composition: `Model`, `ToolRegistry`, prompt,
`ContextManager`, hooks, policy, approval provider/settings, limits, and an
optional `SkillManager`.

`Session` is the single writer for a conversation transcript. It reserves one
active `Run` atomically and owns the ordered pending-input queue. `steer()` and
`follow_up()` accept only `UserMessage`; queued input is consumed once at a
legal turn boundary.

When a new Run also supplies an initial message, pending inputs that predate
`start()` are committed first in receipt order, followed by the Run-local initial
message at the initial legal boundary.

Session and Run async operations are same-event-loop contracts. The ownership
lock does not make a Session generally thread-safe or safe across event loops.
The synchronous ownership lock is never held across an `await`. When multiple
async state locks are needed, their order is pending queue, transcript, then
compaction. Persistence is serialized separately; callers do not hold a state
lock while waiting for persistence, and snapshot collection acquires state
locks only in the canonical order. This ordering is a Runtime invariant.

`Run` starts eagerly, exposes `subscribe()`, `cancel()`, and `result()`, and
always releases Session ownership. Its terminal status is `COMPLETED`, `FAILED`,
or `CANCELLED`; timeout and max-turn termination are coded failures.

`AgentLoop` only orchestrates context, canonical model streams, tools, atomic
transcript commits, and lifecycle hooks. Provider fragments and builtin-specific
behavior stay outside the loop.

A persistent Session tracks runtime and durable revisions separately. Repository
writes use compare-and-swap; the local backend holds a per-session process lock
across revision comparison, unique temporary write, fsync, atomic replacement,
and parent-directory fsync. Persistence failure never rolls back accepted
runtime truth. `SessionSnapshot` contains canonical messages, pending order,
compaction, and metadata, but never live Runtime services or an active Run.

`Session.close()` closes only the current runtime handle. It returns the
outstanding pending receipts but does not clear or persist pending state, so
closing a handle cannot make durable inputs disappear or reappear after restore.
