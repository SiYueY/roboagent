# Runtime Module

`runtime.types` contains cancellation, `RunContext`, `RunState`, status/error,
modality, and media-resolution contracts. Canonical messages and Frozen JSON
live in `message`; canonical model events live in `model`.

Each `RunEventEmitter` has a run-local contiguous sequence, bounded replay, and
independent bounded subscriber queues. `run.started` is first and exactly one
terminal event is last. Late subscribers still receive the retained terminal
event. Event stores persist frozen payloads without merging them into transcript
or Run state.

Long-horizon observation types are `context.compaction_completed`,
`context.compaction_failed`, `session.persisted`,
`session.persistence_failed`, `approval.requested`, `approval.resolved`,
`mcp.connected`, and `mcp.disconnected`. Payloads are JSON-safe and deliberately
exclude approval arguments/reasons, MCP client state and credentials, canonical
message content, and artifact previews.

`RunStatus` is a lifecycle class (`COMPLETED`, `FAILED`, or `CANCELLED`), while
`RunError.code` carries termination causes such as `timeout` and `max_turns`.
