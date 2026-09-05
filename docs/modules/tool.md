# Tool Module

`ToolRegistry` is the sole registry. It validates canonical definitions and
Draft 2020-12 object schemas, preserves registration order, and requires
`replace=True` for replacement.

`ToolExecutor` applies lookup, policy, validation, hooks, events, execution,
normalization, effect recording, and after-hooks in that order. A normal batch
returns one result per call in original order. Cancellation and `FAIL_RUN`
abort without a partial batch.

Normal `ToolBatchResult.effects` and the corresponding final Run effect view also
follow original ToolCall order, regardless of concurrent completion order.

Timeout remains a model-visible timeout result, while its effect record reflects
cleanup evidence independently. An interrupted read-only Tool may be `TIMED_OUT`
or `CANCELLED`; an interrupted side-effecting Tool defaults to `UNKNOWN` unless it
returns canonical content or explicitly raises `ToolExecutionFailure` or
`ToolEffectUnknown`.

The same conservative rule applies after normal execution starts: a generic
exception or invalid output is `FAILED` for a read-only Tool and `UNKNOWN` for a
side-effecting Tool. Only explicit `ToolExecutionFailure` lets the runtime assert
that a side-effecting operation failed.

Filesystem, shell, and `read_skill` are ordinary explicitly registered Tools.
`Agent` registers none by default.

Tool handlers normalize into an ordered `RawToolResult`. A
`ToolResultMaterializer` converts it exactly once before ToolExchange commit;
large or binary blocks become the single canonical
`ArtifactReferenceContent` form through the Session Workspace. Invocation
evidence is fixed before materialization, so materialization failure cannot
change known physical success to `UNKNOWN`.

`read_artifact()` performs normalized URI-to-path resolution and verifies the
stored byte length and SHA-256 digest. Digest-addressed blobs are immutable and
cannot be removed through generic Workspace deletion.

Policy returns `ToolPolicyDecision`. `REQUIRE_APPROVAL` binds an immutable
request to the exact canonical argument digest. Approval rejection, timeout,
provider failure, mismatch, or Run cancellation occurs before execution and
therefore creates no `ToolEffectRecord`. Approval observation events omit
arguments and policy reasons.
