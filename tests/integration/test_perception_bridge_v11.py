"""Specific robot bridge capability checks remain outside generic Vision."""

import asyncio
from examples.embodied.mfr3duo_bridge import Mfr3duoBridge
from roboagent.world import World


class Transport:
    def __init__(self, capabilities=()):
        self.capabilities = capabilities
        self.calls = []

    async def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path))
        if path.endswith("/stop"):
            return {"stopped": True}
        if path.endswith("/state"):
            return {"connection": "online", "base_pose": {"x": 1}}
        return {"capabilities": self.capabilities}


def test_bridge_capability_gates_and_unknown_survives_run_cleanup():
    async def check():
        w = World(
            "w", allowed_sources=("mfr3duo/state",), allowed_scopes=("workspace",)
        )
        transport = Transport()
        bridge = Mfr3duoBridge(w, transport, "robot/1")
        tools = await bridge.tools()
        assert {t.definition.name for t in tools} == {
            "observe_robot_state",
            "get_task_status",
            "stop_robot",
        }
        transport.capabilities = ["manipulation"]
        assert "pick_object" not in {t.definition.name for t in await bridge.tools()}

        async def preflight(snapshot, arguments):
            return True  # Capability registration fixture only.

        bridge.pre_dispatch = preflight
        assert {"pick_object", "place_object"} <= {
            t.definition.name for t in await bridge.tools()
        }
        await bridge.observe_state()
        assert {e.predicate for e in (await w.snapshot()).entries} == {"backend_state"}
        bridge.pending, bridge.uncertain = "request-unknown", True
        assert (await bridge.task_status())["status"] == "unknown"
        assert not any("/operations" in path for _, path in transport.calls)
        assert (await bridge.stop())["stopped"]
        assert bridge.pending is None and not bridge.uncertain

    asyncio.run(check())
