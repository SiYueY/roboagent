# Tool Module

`ToolRegistry` is the sole registry. It validates canonical definitions and
Draft 2020-12 object schemas, preserves registration order, and requires
`replace=True` for replacement.

`ToolExecutor` applies lookup, policy, validation, hooks, events, execution,
normalization, effect recording, and after-hooks in that order. A normal batch
returns one result per call in original order. Cancellation and `FAIL_RUN`
abort without a partial batch.

Filesystem, shell, and `read_skill` are ordinary explicitly registered Tools.
`Agent` registers none by default.
