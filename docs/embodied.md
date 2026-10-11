# World and Interaction integration

Implementation follows the consolidated specification in `design.md`. Environment
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
receive/assertion order. Times are compared only within a scope and clock domain;
incomparable live contradictions remain candidates. Different scopes produce
alternatives rather than a fabricated conflict. An additional scope cannot hide
an established conflict inside another scope. Hypotheses, priors, predictions,
and inferences supported only by them cannot become physical KNOWN facts.

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

V1 relevance uses `self`/`robot`, recent explicit entity references, Host Tool
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
Nested plain JSON mapping parameters support explicit `world_find_entities` filters
in the coding worker; top-level arguments remain closed and all calls still pass
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

- Phase 0: existing code/interfaces inspected and baseline tests executed.
- Phase 1: immutable data contracts, atomicity, idempotency, source/scope membership,
  clock/TTL, late results, conflict/correction, structured search and capacity tests.
- Phase 2: relevance, complete budget, provider/coding union consumers, protected
  uncertainty, transcript separation and fixed-snapshot retry tests.
- Phase 3: breaking imports, bounded frame lookup/conversion, actual Gradio image
  composition and deterministic ASR → canonical Agent → TTS regression.
- Phase 4: fake observation/action/feedback using canonical Agent/Tool; query creates
  no evidence, async acceptance does not claim commit, ACK does not write facts.
- Phase 5: CPU MuJoCo carriage composition: semantic attributes sampled from
  simulator geometry/rgba metadata, semantic target refusal, refreshed geometry,
  changed-target refusal, unrelated-revision acceptance, motion, independent
  evidence, verification, pause/reset and cancellation. No physical robot or live
  cloud perception acceptance is inferred from these tests.
- Phase 6: concurrent atomic writes, bounded capacity rejection, immutable snapshots,
  evidence DAGs, unavailable media metadata, prompt-policy separation, shared
  sessions, nested World binding and permissions, UNKNOWN effects and cancellation.

The tests are in `tests/world`, `tests/context/test_world_context.py`,
`tests/interaction`, `tests/integration/test_embodied_loop.py` and
`tests/integration/test_mujoco_world.py`, alongside the existing regressions.
Cloud VLM/ASR/TTS, RGB-D/TF and a production mfr3duo bridge remain Host integrations
and require their own live acceptance. This release adds no planner, scene graph,
source registry, secondary Runtime, robot safety controller or automatic trigger.

## Local verification result

Final local run: **397 passed, 3 skipped**. The skips are explicitly gated live
Tongyi provider tests (`ROBOAGENT_LIVE_PROVIDER_TEST=1` and `DASHSCOPE_API_KEY`).
The migrated Gradio image tests, deterministic speech roundtrip, both optional
RNNoise/WebRTC DSP tests and CPU MuJoCo tests ran. The local environment included
coding, Gradio, embodied, speech and speech-webrtc extras.
The existing `audioop` deprecation warning remains on Python 3.12.

`ruff check roboagent tests examples`, configured `mypy` (51 source files), Python
compilation, dependency lock validation and `git diff --check` pass. CI installs
coding, Gradio and embodied extras so the new simulation/image regressions run.

A local CPU measurement with 512 distinct entities/claims and 512 observations
committed in 125.80 ms total; 20 full snapshots had a 4.83 ms median and 5.24 ms
maximum. These are measurements of this bounded fixture on the local host, not a
real-time latency guarantee or a robot/cloud throughput benchmark.
