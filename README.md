# RoboAgent

RoboAgent is a native Python runtime for tool-calling robot agents. It has no
LangChain, LangGraph, LangSmith, or DashScope SDK dependency.

## Compose explicitly

Configuration, model selection, tools, skills, and observability belong to the
application layer. The Agent API has no configuration factory:

```python
from roboagent.agent import Agent
from roboagent.model import create_chat_model

model = create_chat_model(registry=config.to_model_registry())
agent = Agent(model, tools=robot_tools, system_prompt="Operate safely.")
session = agent.new_session()

run = session.start("Report the current pose.")
async for event in run.events():
    handle(event)
result = await run.result()
```

`Agent` is immutable. `AgentSession` owns a conversation transcript and accepts
one run at a time. `AgentRun` owns cancellation, streamed events, and the final
`AgentRunResult`. `run.result()` completes whether or not an event stream is
consumed. Independent sessions may run concurrently.

Use `session.subscribe(...)` for optional best-effort UI observers, or attach an
`EventRecorder` backed by `MemoryEventStore` or `JsonlEventStore`. Subscriber
queues are bounded: a slow subscriber is disconnected instead of stalling a
robot action. Use `AgentHooks` for context transformation and tool policy.
Tools execute in the order requested by the model; the first failed tool call
short-circuits the remaining calls in that batch.

`run.cancel(reason="user")` requests cooperative cancellation. A cancellation
request does not itself stop external robot hardware: a tool handler must
cancel and await its own external operation. Set `Agent(..., run_timeout=...)`
to use the same cooperative path with `reason="timeout"`.

## Examples

Runnable examples live in [examples](examples/README.md). Optional integrations
such as Gradio are deliberately not part of RoboAgent's core dependencies.
