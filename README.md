# RoboAgent 1.2

RoboAgent is a provider-neutral Python runtime for multimodal, tool-calling
agents. Version 1.2 extends the canonical V1.1 Runtime Kernel with bounded
context compaction, durable Sessions, artifact materialization, MCP adaptation,
and approval without introducing a parallel runtime.

## Explicit composition

```python
from roboagent import Agent
from roboagent.context import PromptInput
from roboagent.message import UserMessage
from roboagent.model import create_model
from roboagent.tool import ToolRegistry

model = create_model(registry=config.to_model_registry())
agent = Agent(
    model,
    tool_registry=ToolRegistry(robot_tools),
    prompt=PromptInput("Operate safely."),
)
session = agent.new_session()

run = session.start(UserMessage("Report the current pose."))
async for event in run.subscribe():
    handle(event.type, event.payload)
result = await run.result()
```

`Agent` is immutable and registers no builtin tools. `Session` owns the canonical
transcript, compaction state, Workspace, persistence repository, a pending
`UserMessage` queue, and at most one active `Run`. A `Run`
owns cancellation, bounded event subscriptions, effects, usage, and its final
`RunResult`.

Tools, filesystem/shell builtins, and `read_skill` are explicit registrations.
Tool calls execute concurrently only when every tool in the batch declares
`CONCURRENT`; results are committed in original call order as one complete
tool-exchange block.

Messages support text, image, audio, and file content. Model adapters declare
their capabilities and reject unsupported inputs before issuing a request.

Runnable integrations live in [examples](examples/README.md). The normative
runtime contract is [docs/roboagent_v1.2.md](docs/roboagent_v1.2.md).
