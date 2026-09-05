# Agent Module

`Agent` is immutable capability composition: `Model`, `ToolRegistry`, prompt,
`ContextManager`, hooks, policy, limits, and an optional `SkillManager`.

`Session` is the single writer for a conversation transcript. It reserves one
active `Run` atomically and owns the ordered pending-input queue. `steer()` and
`follow_up()` accept only `UserMessage`; queued input is consumed once at a
legal turn boundary.

Session and Run async operations are same-event-loop contracts. The ownership
lock does not make a Session generally thread-safe or safe across event loops.

`Run` starts eagerly, exposes `subscribe()`, `cancel()`, and `result()`, and
always releases Session ownership. Its terminal status is `COMPLETED`, `FAILED`,
or `CANCELLED`; timeout and max-turn termination are coded failures.

`AgentLoop` only orchestrates context, canonical model streams, tools, atomic
transcript commits, and lifecycle hooks. Provider fragments and builtin-specific
behavior stay outside the loop.
