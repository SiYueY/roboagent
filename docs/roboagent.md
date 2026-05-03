# RoboAgent

The Brain of an Autonomous Robot System.

## Architecture

RoboAgent is organized around seven runtime subsystems:

- `config`
  Loads and validates top-level application settings, model entries, skill
  sources, permission policy, and sub-agent profiles.
- `model`
  Registers configured provider-backed chat models and creates LangChain chat
  model instances.
- `tool`
  Wraps LangChain `BaseTool` objects with management metadata, registry lookup,
  and context-aware resolution.
- `skill`
  Discovers portable `SKILL.md` packages, stores runtime skill records, selects
  skills for requests, and executes typed handlers through `SkillExecutor`.
- `agent`
  Provides two assembly entry points: `create_roboagent_runtime(...)` for
  config-driven construction and `AgentBuilder` for pure-argument construction.
- `middleware`
  Provides runtime cross-cutting behavior such as skill context injection, tool
  error normalization, and run journaling.
- `runtime`
  Tracks run records and event streams for local execution observability through
  `RunManager` and `RunEventStore`.

## Invocation Flow

The configuration-driven local runtime flow is:

`AppConfig -> create_roboagent_runtime -> ModelRegistry -> SkillManager -> ToolManager -> AgentBuilder -> create_agent`

`create_roboagent_runtime(...)` resolves the model registry, loads configured
skills, creates optional runtime tracking objects, builds the middleware chain,
and delegates the final graph construction to `AgentBuilder`.

At request time, the intended capability flow is:

`User -> Agent -> Skill selection -> Tool resolution -> Skill execution or model response`

When `RuntimeFeatures.run_journal=True`, the runtime also records coarse
agent/model/tool boundary events:

`RuntimeContext -> RunManager -> RunJournalMiddleware -> RunEventStore`

Skill execution is always bounded by:

- entrypoint resolution
- Pydantic input validation
- permission allowlist checks
- handler invocation
- Pydantic output validation
- structured execution result logging

## Dependency Direction

The module dependency direction should remain:

- `agent -> config, model, skill, tool, middleware, runtime`
- `middleware -> skill, runtime`
- `skill -> config` only for public configuration objects
- `tool` remains independent from `agent` and `skill` runtime internals
- `config` remains independent from runtime execution modules
- `runtime` remains independent from `agent`, `skill`, `tool`, and `middleware`

Lower-level schema and registry modules should not import manager or agent
assembly modules.

## Runtime Features

`RuntimeFeatures` controls optional assembly behavior:

- `tool_resolution`
  Injects a `ToolManager` so active skill `allowed_tools` can narrow model-bound
  tools.
- `skill_context`
  Loads configured skills and injects active skill context through
  `SkillContextMiddleware`.
- `run_journal`
  Creates a run record and attaches `RunJournalMiddleware`.
- `guardrails`, `subagent`, `sandbox`
  Reserved feature switches for future runtime layers.

## Runtime Observability

The current observability layer is intentionally local and in-memory:

- `RunManager` owns run records and status transitions.
- `MemoryRunEventStore` stores ordered events with per-thread sequence numbers.
- `RunJournalMiddleware` records `agent_start`, `agent_end`, `model_start`,
  `model_end`, `model_error`, `tool_start`, and `tool_end`.

Persistent stores, cancellation policies, and recovery semantics are future
runtime extensions.

## Public Runtime APIs

Primary entry points:

- `roboagent.config.AppConfig`
- `roboagent.config.load_app_config(path=None)`
- `roboagent.model.factory.create_chat_model(name=None, *, registry, **kwargs)`
- `roboagent.skill.SkillManager`
- `roboagent.skill.SkillExecutor`
- `roboagent.tool.ToolManager`
- `roboagent.tool.ResolutionContext`
- `roboagent.middleware.SkillContextMiddleware`
- `roboagent.middleware.ToolErrorHandlingMiddleware`
- `roboagent.middleware.RunJournalMiddleware`
- `roboagent.middleware.build_runtime_middlewares`
- `roboagent.runtime.RunManager`
- `roboagent.runtime.RunStatus`
- `roboagent.runtime.MemoryRunEventStore`
- `roboagent.runtime.RunEventStore`
- `roboagent.agent.RuntimeFeatures`
- `roboagent.agent.RuntimeContext`
- `roboagent.agent.create_roboagent_runtime(...)`
- `roboagent.agent.AgentBuilder`

`create_roboagent_runtime(...)` is the configuration-driven entry point.
`AgentBuilder` remains the pure-argument assembly entry point.
