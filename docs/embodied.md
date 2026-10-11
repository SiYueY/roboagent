# Embodied reference integration

This is a reference application of RoboAgent primitives, not a Core specification.
Embodied Reference Agent validates World/Runtime composition; it does not define Core APIs.
The application contract is [Embodied reference application](examples/embodied.md);
its independent generic cognition contract is [World V1.1](world/world_v1.1.md).
[World V1.0](world/world_v1.0.md) is historical embodied design context, not a
normative Core contract. Environment
cognition belongs to World, conversation to Session, and execution/effects to the
existing Run/Execution. Host adapters and observation/action Tools remain explicit.

## Host composition

```python
from roboagent import Agent
from roboagent.tool import ToolRegistry
from roboagent.world import World, create_world_tools

world = World(
    "robot-1",
    allowed_sources=("robot/state", "host/perception"),
    allowed_scopes=(None, "workspace"),
    clock_resolver=host_clock_resolver,
)
agent = Agent(model, world=world, tool_registry=ToolRegistry(create_world_tools(world)))
```

Hosts authenticate writers and assign their source, scope and World binding.
World defaults to rejecting every unconfigured source; source strings are not
credentials. Binding a World grants no implicit Tool capability. Explicit query
Tools are read-only and acquire no new evidence. Register observation/action
Tools separately under existing policy and approval.

`observe()` atomically ingests immutable evidence and its synchronous claim set.
Retrying the same transaction is idempotent. Changed evidence or claim identities,
missing evidence, cyclic evidence/correction chains, unauthorized membership and
capacity overflow reject the entire transaction without changing revision.
Delayed perception uses `add_claims()`. Old sampled results cannot win through
receive/assertion order. State evolution selects the latest resolved sample only within the same
subject/predicate/scope/source/grounding/clock stream. Independent sources and
equal-time contradictory samples remain candidates. Each scope has its own entry;
queries use exact scope matching and never implicitly merge namespaces. An
additional scope cannot hide an established conflict inside another scope. Hypotheses, priors, predictions,
and inferences supported only by them cannot become grounded KNOWN facts.

World JSON results preserve relation values with `value_kind="entity_ref"` and
`value={"entity_ref": {"id": "table_1"}}`; arbitrary JSON values have
`value_kind="json"`. This preserves the closed value union even when literal
JSON happens to resemble an EntityRef, including transaction identity checks.

All capacity defaults are finite. Capacity exhaustion rejects writes; this release
does not automatically evict evidence or active correction chains. The payload
byte limit applies to total retained serialized data as well as individual writes.
Large media stays in Host-owned storage. `ResourceRef.available=False` records an
already unavailable resource while preserving metadata. World never fetches,
opens, decodes, expires or deletes Host media. Hosts choose retention/lifecycle
and supply new evidence; this release has no media store or World persistence.

Claim TTL uses `valid_at` and `valid_for_ns` in a matching clock domain.
Unavailable clocks yield UNKNOWN when TTL cannot be evaluated. A wrong-domain or
failing resolver raises `clock_unavailable`. Snapshot freezes freshness without
changing revision; do not cache current cognition indefinitely by revision alone.
Clock restart/reset requires a new identity. A future `valid_at` in an available
clock domain is UNKNOWN until that time, even when no automatic TTL is set.
World and all direct writers/readers must share one asyncio loop.

## Context and budget

AgentLoop captures Session input and one WorldSnapshot per Model Turn, including
compaction retries. ContextManager owns selection, serialization and the full
input budget; the Loop does not interpret entries. Snapshot contains the complete
bounded current view, ordered by last canonical update with deterministic ties.
Context projection defaults to 64 entries plus all relevant uncertain entries,
then prunes low-priority entries to fit the full request. It preserves uncertainty
status/value/candidates/provenance and reports omissions explicitly.

Relevance uses recent explicit entity references and Host Tool
annotations, uncertain state, recent canonical updates and stable ties. Conversation
references use `JsonContent({"entity_ref": {"id": "cube_7"}})`; a Tool can declare
`"x-world-entities": ["cube_7"]` in its Host-authored input schema. World search
results' `entity` fields, entry `subject` fields and typed relation values are
recognized as explicit references. Tool parameter schemas can mark string IDs
with `"x-world-entity-ref": true`; `world_query.entity_id` uses this annotation.
References from a different `world_id` are excluded from the current World's
relevance ranking. Literal JSON relation lookalikes are not treated as links.
Text is never used for hidden natural-language grounding. Agent reasoning or an
explicitly registered query Tool performs disambiguation.

`FullContextManager` and `WindowContextManager` accept optional `budget` and
`estimator`; World composition defaults to an 8192-token window when the Model
has none. Pure text composition without an explicit budget or encoding projection
retains its behavior.
`CompactingContextManager` first removes low-relevance World entries, then uses
existing conversation compaction. Relevant UNKNOWN/STALE/CONFLICTED entries are
retained, including when this makes the input impossible to fit: preparation then
fails explicitly. For multimodal inputs configure a media-aware estimator; the
conservative estimator refuses to guess media token costs. Final budget checks
include system instructions, Tool schemas, Skill metadata, data and transcript.

A Host can attach a pure `Agent.model_input_projection` callable to describe the
provider's complete encoding envelope. AgentLoop captures this configuration in
ContextRequest; ContextManager estimates both canonical contracts and the projected
envelope and retains the larger bound. It does not store the projection in Session.
The coding factory preserves the base Agent's World and ContextManager, composes
its provider encoding projection, and includes bounded reset and fixed protocol
correction messages before budget validation. The adapter chooses no separate token
window/reserve and performs no token pruning. Every retry keeps the same World data.
Tagged JSON filter arrays support explicit `world_find_entities` filters
in the coding worker; EntityRef filters remain distinct from ordinary JSON, top-level arguments remain closed and all calls still pass
through canonical policy, approval and nested execution.

`ContextDataSegment` belongs to the closed ModelContext union. Providers encode it
as user-role untrusted JSON before transcript/summary. It cannot split a Tool
exchange and never enters transcript, persistence or summarizer input. Coding
projection and token estimators handle the same segment. Host policy and approval
remain authoritative regardless of environmental text.

## Interaction

Use `roboagent.interaction.vision` and `roboagent.interaction.speech`. No legacy
imports or compatibility layers are provided. VisionFrame has no persistent ID;
its `captured_at` is process-local monotonic time. VisionBuffer bounds count,
bytes and age, performs latest/nearest lookup and deterministic drop-old retention.
`image_content_from_frame()` validates MIME and media size and creates canonical
ImageContent. World evidence identity and clock attribution are assigned separately
by a Host adapter. Speech retains its ASR/Agent/TTS and streaming lifecycle.

## Validation boundaries

World V1.1 implementation follows four phases:

- Phase 1: exact scope identities, immutable lookup, state-stream resolution,
  mixed candidates/provenance, typed filters, POSSIBLE search and Context consumers.
- Phase 2: one source Snapshot/message_id per Model Turn, nested propagation,
  fresh Child Agent capture; these causal-context primitives remain generic.
- Phase 3: Host-owned PhysicalPolicy slots, SERIAL wrappers and lifecycle cleanup;
  actual MuJoCo contact, object motion, release, independent post-action
  samples, evidence completeness, changed-target refusal, cancellation and stability.
- Phase 4: structured Model perception, capture-bound association, RGB-D/skew and
  historical TF, synchronous Observation Tools, and the roserver state/action/task
  bridge with explicit live preflight. These adapter tests use controlled fixtures.

Existing Interaction, coding, speech and provider regressions remain part of the
full suite. Tests live in `tests/world`, `tests/context`, `tests/runtime` and
`tests/integration`, including `test_decision_context_v11.py`,
`test_tabletop_v11.py` and `test_perception_bridge_v11.py`.

Local deterministic and real MuJoCo evidence establish Levels 0–1. Live Model
acceptance uses `ROBOAGENT_EMBODIED_LIVE=1` and the configured Model registry.
Real cameras, calibration, clock mapping, object association, historical TF and
robot preflight require deployment evidence; fixtures do not establish Levels 3–4.
No planner, source registry, second Runtime or automatic sensor-triggered Run is added.

## Local verification result

Final World V1.1 local run: **471 passed, 4 skipped**. Three skips are gated
Tongyi provider tests; one is the cloud Model tabletop acceptance test
(`ROBOAGENT_EMBODIED_LIVE=1`). Cloud tests require explicit configuration and
credentials and are counted separately from deterministic / MuJoCo tests.
The existing Python 3.12 `audioop` deprecation warning remains.

`ruff check roboagent tests examples`, configured `mypy` (56 source files), Python
compilation, offline dependency lock validation and `git diff --check` pass.
The default `python -m examples.embodied.app` completed actuator-driven pick/place
and independently verified inside, release and stability. This is Level 0–1
acceptance; live Model/VLM, real sensor/TF/bridge and physical hardware Level 2–4
acceptance was not performed in this run.

The real bridge intentionally requires a trusted live preflight callback before
registering manipulation. Missing full object pose, timestamps or trustworthy TF must
leave action readiness unavailable. Clock epoch restart does not silently retire
incomparable old evidence. No fallback guesses or fake successful manipulation
are introduced to bypass these limits.
