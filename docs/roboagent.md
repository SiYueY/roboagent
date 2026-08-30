# RoboAgent Runtime

RoboAgent is a native asynchronous tool-calling runtime. The application creates
an immutable `Agent`, then creates one or more independent `AgentSession`
objects. Each session creates an `AgentRun` for a prompt; `run.events()` opens
an event stream, `await run.result()` returns a terminal result, and
`run.cancel()` requests cooperative cancellation.

`runtime` defines framework-independent messages and provider requests. `model`
adapts OpenAI-compatible endpoints, `tool` validates and executes native tools,
and `agent/loop.py` coordinates strictly sequential tool calls. `runtime.event`
is the sole lifecycle event model, while `EventRecorder` can persist it to an
`EventStore`. Configuration and skills are application-level composition choices.
