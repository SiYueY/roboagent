# Runtime Module

`runtime.types` contains the immutable contracts shared by model, tool and
agent layers: messages, tool calls/definitions/results, model contexts and
model stream events. It does not contain Agent lifecycle state.

`runtime.event` is the only lifecycle event model. Every event has a run-local,
strictly increasing sequence and timestamp; exactly one `AgentCompletedEvent`
ends a run. `runtime.store` supplies `EventStore`, `MemoryEventStore`,
`JsonlEventStore`, and the best-effort `EventRecorder` observer. JSONL stores
can be reopened and queried by run ID. Store failures disable the recorder
without interrupting the Agent.
