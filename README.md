# RoboAgent 1.3

RoboAgent is a provider-neutral Python runtime for multimodal, tool-calling
agents. Version 1.3 extends the same canonical Runtime with nested execution,
Agent-as-Tool, a transactional `apply_patch` builtin, and a process-isolated
coding reference harness. It does not introduce a second Agent, Session, Run,
Loop, Model, Tool, policy, approval, event, or effect runtime.

## Explicit composition

```python
from roboagent import Agent
from roboagent.config import load_app_config
from roboagent.context import PromptInput
from roboagent.message import UserMessage
from roboagent.model import create_model
from roboagent.tool import ToolRegistry

config = load_app_config()
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
runtime contract is [docs/roboagent_v1.3.md](docs/roboagent_v1.3.md).

## Coding reference agent

Install the terminal dependency and run one task against a workspace:

```bash
uv sync --extra coding
uv run python -m examples.coding --workspace . "Inspect the repository and explain the test path."
```

Interactive mode supports `/steer TEXT`, `/follow-up TEXT`, `/cancel`, and
`/quit`. Filesystem, shell, and `apply_patch` calls remain canonical RoboAgent
Tools and pass through policy and approval. Python code runs in a separate
worker and can invoke those Tools only through IPC and nested Tool execution.

Restricted mode is the default and is a capability boundary for ordinary
model-generated code, not a hostile-code sandbox. `--unsafe-python` enables
trusted execution and prints a prominent warning; trusted execution is always
side-effecting and retry-unsafe.

See [examples/coding/README.md](examples/coding/README.md) for configuration,
evaluation, attribution, and safety details.
