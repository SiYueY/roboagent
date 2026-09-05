# Tool Module

`ToolRegistry` is the sole registry. It validates canonical definitions and
Draft 2020-12 object schemas, preserves registration order, and requires
`replace=True` for replacement.

`ToolExecutor` applies lookup, policy, validation, hooks, events, execution,
normalization, effect recording, and after-hooks in that order. A normal batch
returns one result per call in original order. Cancellation and `FAIL_RUN`
abort without a partial batch.

Timeout remains a model-visible timeout result, while its effect record reflects
cleanup evidence independently. An interrupted read-only Tool may be `TIMED_OUT`
or `CANCELLED`; an interrupted side-effecting Tool defaults to `UNKNOWN` unless it
returns canonical content or explicitly raises `ToolExecutionFailure` or
`ToolEffectUnknown`.

Filesystem, shell, and `read_skill` are ordinary explicitly registered Tools.
`Agent` registers none by default.
