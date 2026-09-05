# Runtime Module

`runtime.types` contains cancellation, `RunContext`, `RunState`, status/error,
modality, and media-resolution contracts. Canonical messages and Frozen JSON
live in `message`; canonical model events live in `model`.

Each `RunEventEmitter` has a run-local contiguous sequence, bounded replay, and
independent bounded subscriber queues. `run.started` is first and exactly one
terminal event is last. Late subscribers still receive the retained terminal
event. Event stores persist frozen payloads without merging them into transcript
or Run state.

`RunStatus` is a lifecycle class (`COMPLETED`, `FAILED`, or `CANCELLED`), while
`RunError.code` carries termination causes such as `timeout` and `max_turns`.
