# RoboAgent Runtime

RoboAgent is a native asynchronous tool-calling runtime. The application creates
an immutable `Agent`, then creates one or more independent `AgentSession`
objects. Each session creates an `AgentRun` for a prompt; runs stream events,
return a terminal result, and can be cancelled.

`runtime` defines framework-independent messages and provider requests. `model`
adapts OpenAI-compatible endpoints, `tool` validates and executes native tools,
and `agent/loop.py` coordinates strictly sequential tool calls. Configuration,
skills, and journaling are application-level composition choices.
