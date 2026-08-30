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
async for event in run:
    handle(event)
result = await run.result()
```

`Agent` is immutable. `AgentSession` owns a conversation transcript and accepts
one run at a time. `AgentRun` owns cancellation, streamed events, and the final
`AgentRunResult`. Independent sessions may run concurrently.

Use `session.subscribe(...)` for optional best-effort journal or UI observers.
Use `AgentHooks` for context transformation and tool policy. Tools execute in
the order requested by the model.
