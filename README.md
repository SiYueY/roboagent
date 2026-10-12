# RoboAgent 1.3

RoboAgent is a minimal, composable, explicit, extensible, provider-neutral Agent
Harness for building multimodal, tool-using agents. Version 1.3 extends the same
canonical Runtime with nested execution,
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
    tool_registry=ToolRegistry(tools),
    prompt=PromptInput("Follow the user's request."),
)
session = agent.new_session()

run = session.start(UserMessage("Inspect the available information and summarize it."))
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

## Environment cognition and Interaction

An optional Host-owned `World` supplies immutable current cognition to each Model
Turn without changing Session transcripts. Query tools remain explicit. Direct
media uses `roboagent.interaction.vision` and `roboagent.interaction.speech`.
World V1.1 adds exact scopes, typed entity search and Model Turn source-snapshot
propagation; see the [generic World contract](docs/world/world_v1.1.md).

## Reference applications

[Chat](examples/chat/README.md), [Coding](examples/coding/README.md), and
[Embodied](examples/embodied/README.md) are consumers of the same primitives.
The [embodied reference integration](docs/embodied.md) uses a CPU MuJoCo tabletop
and explicit sensor/backend adapters. Its physical action policy and verification
rules belong to the reference application, not the generic Core.

## Visual perception

The optional `vision` extra provides CPU geometry and cloud semantic analysis via
existing Model interfaces. Capture/clock/calibration and source authorization are
Host inputs. Results explicitly distinguish surface points, visible centroids,
principal axes and validated object poses; temporal tracking and World publication
remain separate. See [Vision V1](docs/vision/vision_v1.0.md).

```bash
uv sync --extra vision
```
