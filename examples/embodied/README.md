# Embodied reference composition

Run real MuJoCo physics on CPU using the existing Agent, Session, Run and Tool:

Run the commands below from the **`roboagent` repository root**, where
`pyproject.toml` defines the `embodied` extra. If your terminal is in the sibling
`roserver` repository, first run `cd ../roboagent`.

```bash
uv sync --extra embodied
uv run --extra embodied python -m examples.embodied.app
```

The constrained carriage has sampled semantic attributes and a world-frame
position. Geometry type and RGBA are read from actual simulator metadata and
included in the observation evidence; they are not fixed semantic claims. Simple
palette colors map deterministically to names, while other colors retain measured
RGBA. A fresh red target is required before the deterministic Model requests motion. `drive_carriage` revalidates the target pose against both current World
cognition and live simulator geometry, refreshing derived positions immediately
before the command, then drives a limited actuator. Its
controller completion does not write World state. `observe_state` independently
samples the new position and commits evidence before returning; the next Model
Turn verifies the result. The example uses a deterministic Model so it requires
no API credentials. Replace it with a configured RoboAgent Model for language
reasoning; keep Host source binding, tools and physical validation explicit.

The adapter uses a simulation clock domain with a new identity on reset. Paused
physics does not expire claims because host wall time advances. Run cancellation
stops issuing actuator commands and preserves World cognition; it does not
verify physical standstill. This small scene demonstrates one bounded axis, not
robot grasping, ROS TF, RGB-D localization, collision planning, or an mfr3duo driver.

MuJoCo arrays are copied into immutable evidence using the
[official Python API](https://mujoco.readthedocs.io/en/stable/python.html).
The adapter owns the simulator; `World` never loads models or accesses devices.
See [World integration](../../docs/embodied.md) for contracts and validation.
