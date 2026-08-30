# Agent Module

`Agent` is an immutable definition of a model, tool set, system prompt, hooks,
and turn limit. It owns no conversation state. Call `new_session()` to create an
`AgentSession`; sessions own their transcript, observers, and one active run.

`AgentRun` owns one cancellable execution. It is an async iterator of lifecycle
events and exposes `await result()`. The internal `loop.py` performs sequential
model/tool turns and does not own persistent session state.

Configuration is deliberately outside this module: applications compose models,
tools, optional skill transforms, and journal observers explicitly.
