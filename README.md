# RoboAgent

The Brain of an Autonomous Robot System.

RoboAgent is a Python package for assembling robot-oriented agent runtimes from
configured chat models, managed tools, reusable skills, and sub-agent profiles.

## Install

```bash
uv sync
```

The package requires Python 3.12 or newer.

## Configuration

RoboAgent loads application configuration from `config.yaml` by default, or from
`ROBOAGENT_CONFIG_PATH` when that environment variable is set.

```yaml
default_model: openai-main
models:
  - name: openai-main
    provider: openai
    params:
      model: gpt-4o-mini

skills:
  sources:
    - ./skills
  allowed_permissions:
    - tool:file.read
    - tool:map.read

subagents:
  - id: planner
    role: navigation planning
    allowed_tools:
      - map.read
    allowed_skills:
      - nav-plan
```

## Minimal Usage

```python
from roboagent.agent import RuntimeContext, create_roboagent_runtime
from roboagent.config import load_app_config

config = load_app_config("config.yaml")
agent = create_roboagent_runtime(
    config,
    runtime_context=RuntimeContext(
        model_name=config.default_model,
        system_prompt="Operate as a concise robot task agent.",
    ),
)
```

Use `AgentBuilder` directly when all runtime dependencies are already created:

```python
from roboagent.agent import AgentBuilder

agent = AgentBuilder(
    model=model,
    skills=skills,
    tools=tools,
    system_prompt="Operate as a concise robot task agent.",
).build()
```

## Skills

Skills are discovered from `SKILL.md` files under configured source
directories. Executable skills declare an `entrypoint` and may declare
permissions and schema references in frontmatter metadata.

```md
---
name: nav-plan
description: Generate navigation plans for robot movement.
allowed-tools: map.read pose.read
metadata:
  version: 0.1.0
  required-permissions: tool:map.read tool:pose.read
  entrypoint: robot_skills.nav:run
---

Use this skill when the task requires route or waypoint planning.
```

Use `SkillExecutor` or `SkillManager.execute(...)` to invoke executable skills
with Pydantic-validated input and output.

## Runtime Assembly

`create_roboagent_runtime(...)` is the configuration-driven runtime factory.
`AgentBuilder` is the pure-argument assembly API. It resolves active skills,
filters out disabled or deprecated skills, asks `ToolManager` for tools allowed
by the active skills, and assembles native context transforms and event subscribers.

Skill routing context is supplied by a native context transform, and tool failures
are normalized by the agent loop.

Enable lightweight run journaling with `RuntimeFeatures(run_journal=True)`:

```python
from roboagent.agent import RuntimeContext, RuntimeFeatures, create_roboagent_runtime
from roboagent.runtime import MemoryRunEventStore, RunManager

event_store = MemoryRunEventStore()
run_manager = RunManager()

agent = create_roboagent_runtime(
    config,
    runtime_context=RuntimeContext(
        thread_id="thread-1",
        event_store=event_store,
        run_manager=run_manager,
    ),
    features=RuntimeFeatures(run_journal=True),
)
```

The default journal records coarse agent/model/tool events to an in-memory
`MemoryRunEventStore`. Persistent stores are intentionally left for later
runtime extensions.
