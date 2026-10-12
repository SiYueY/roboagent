# Embodied reference composition

This is a reference application of RoboAgent primitives, not a Core specification.

Run from the **roboagent repository root**:

```bash
uv sync --extra embodied
uv run --extra embodied python -m examples.embodied.app
```

This CPU MuJoCo tabletop contains two cubes, a basket and a servo-driven gantry
with two fingers. Pick/place uses actuator control and physical contact; object
poses and holding state are never assigned to simulate success. An explicit
observation commits geometry, contacts and velocities to World. Placement requires
post-action evidence of inside, release and stability across two distinct samples.
The default deterministic Model verifies Levels 0–1; it does not establish natural
language grounding or real robot integration.

For Level 2, configure the existing `config.yaml` Model registry and provider
credentials, then run:

```bash
uv run --extra embodied python -m examples.embodied.app --cloud
```

`contracts.py` validates selection/readiness evidence against the immutable source
snapshot and current World. Physical policy uses existing SERIAL execution;
nested wrappers need explicit authorization and share a Host-owned `PhysicalPolicy`
turn slot. Register the same policy instance as `tool_policy` and in `hooks`; the
Host Tools must use it for decision validation. Run hooks/resources clean policy
state after canonical settlement; Core has no per-turn action limit.
The original one-axis carriage remains available as
`python -m examples.embodied.mujoco` for its existing regressions.

Host integrations are explicit:

- `perception.py` is now an explicit application composition over first-party
  `SemanticAnalyzer`, `SpatialAnalyzer`, `VisualTracker`, `VisionWorldAdapter` and
  `create_vision_tools`. Install `--extra vision` for this optional sensor path.
  Hosts supply capture/calibration, an explicitly activated Host-owned tracker,
  controller identity mapping, historical TF,
  targeted query resolution and known-object PnP correspondences. Visible points
  and PCA axes never become object poses. Only explicit category/color/full-pose
  application mappings are provided; semantic spatial relations stay namespaced.
- `mfr3duo_bridge.py`: `Mfr3duoBridge` uses roserver `/api/v1/robots/...` state, operations
  and stop endpoints. Manipulation Tools are registered only when the server advertises
  manipulation and the deployment supplies a trusted `pre_dispatch` callback for
  live geometry, readiness, resource ownership and collision checks. The current
  roserver pose polling response lacks sampling timestamps, so it cannot replace
  that callback. Place rejects roll/pitch that the server yaw-only pose API cannot
  represent. Accepted operations remain pending until terminal confirmation;
  confirmed success/cancellation clears the pending task using the actual operation
  identity and terminal record. Failed or uncertain termination requires confirmed
  stop. Submission, status and stop share Host admission; evidence and pose age
  are rechecked after awaited preflight work immediately before dispatch. Ordinary state polling is not physical verification evidence.

Hardware capture, calibration, historical TF and a trusted complete pose must be
supplied by the deployment. Level 3/4 require actual sensor/robot evidence; passing
adapter tests cannot establish those levels. No sensor stream starts Agent Runs.

See the [generic World V1.1](../../docs/world/world_v1.1.md),
[embodied application contract](../../docs/examples/embodied.md), and
[World integration](../../docs/embodied.md) for ownership and acceptance contracts.
