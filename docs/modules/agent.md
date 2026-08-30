# Agent Module

`Agent` is an immutable definition of a model, tool set, system prompt, hooks,
and turn limit. It owns no conversation state. Call `new_session()` to create an
`AgentSession`; sessions own their transcript, observers, and one active run.

`AgentRun` owns one cancellable execution. `run.events()` creates an independent
async lifecycle-event stream and `await run.result()` never requires an event
consumer. `run.cancel(reason="user")` requests cooperative cancellation; tools
remain responsible for stopping any external robot operation. `run_timeout` is
disabled by default and uses the same cancellation chain when configured.

Each `ContextTransform` receives both the immutable `ModelContext` and the
run's `CancellationToken`; one-argument transforms are not supported.

The internal `loop.py` performs sequential model/tool turns and does not own
persistent session state. The first error in a tool batch short-circuits later
calls; a `ToolExecutionResult(stop_run=True)` ends the run. Model-visible tool
errors use stable codes: `unknown_tool`, `invalid_arguments`, `policy_denied`,
`cancelled`, `timeout`, `execution_error`, and `backend_error`.

Configuration is deliberately outside this module: applications compose models,
tools, optional skill transforms, and `EventRecorder` observers explicitly.
