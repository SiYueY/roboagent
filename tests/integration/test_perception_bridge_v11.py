import asyncio
from datetime import datetime, timezone

import pytest

from examples.embodied.mfr3duo_bridge import Mfr3duoBridge
from examples.embodied.perception import Detection, RGBDAdapter, RGBDSample
from roboagent.world import (
    EntityRef,
    Observation,
    ObservationRef,
    ObservationTime,
    World,
)


def sample(depth_clock="camera", depth_time=10):
    return RGBDSample(
        Observation(
            ObservationRef("camera", "1"),
            "rgbd",
            ObservationTime("camera", 10),
            datetime.now(timezone.utc),
            {"depth_ref": "host://depth-1", "rgb_ref": "host://rgb-1"},
            "workspace",
        ),
        ObservationTime(depth_clock, depth_time),
        2,
        2,
        (1.0, 1.0, 1.0, 1.0),
        (1.0, 1.0, 0.0, 0.0),
        "camera_frame",
    )


@pytest.mark.parametrize(
    "failure", ["skew", "clock", "tf", "depth", "bbox", "geometry_attribute"]
)
def test_geometry_failures_are_atomic(failure):
    async def check():
        from dataclasses import replace

        w = World("w", allowed_sources=("camera",), allowed_scopes=("workspace",))

        async def transform(frame, time, point):
            assert frame == "camera_frame" and time == ObservationTime("camera", 10)
            return None if failure == "tf" else point

        adapter = RGBDAdapter(
            w, source="camera", scope="workspace", max_skew_ns=2, transform=transform
        )
        captured = sample(
            "other" if failure == "clock" else "camera",
            100 if failure == "skew" else 10,
        )
        if failure == "depth":
            captured = replace(captured, depth_metres=(0.0, 0.0, 0.0, 0.0))
        detection = Detection(
            EntityRef("cube"),
            "cube",
            {"pose": {}} if failure == "geometry_attribute" else {"color": "red"},
            (0, 0, 4, 4) if failure == "bbox" else (0, 0, 2, 2),
        )
        with pytest.raises(ValueError):
            await adapter.commit(captured, (detection,))
        assert (await w.snapshot()).revision == 0

    asyncio.run(check())


def test_rgbd_commit_retains_capture_clock_and_does_not_invent_orientation():
    async def check():
        w = World("w", allowed_sources=("camera",), allowed_scopes=("workspace",))

        async def transform(frame, time, point):
            return (point[0] + 1, point[1], point[2])

        adapter = RGBDAdapter(
            w, source="camera", scope="workspace", max_skew_ns=2, transform=transform
        )
        receipt = await adapter.commit(
            sample(),
            (Detection(EntityRef("cube"), "cube", {"color": "red"}, (0, 0, 2, 2)),),
        )
        assert receipt.changed
        snapshot = await w.snapshot()
        assert all(
            e.valid_at == ObservationTime("camera", 10) for e in snapshot.entries
        )
        assert not any(e.predicate == "pose" for e in snapshot.entries)

    asyncio.run(check())


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


def test_cloud_output_is_bound_to_original_capture_and_association():
    async def check():
        import json
        from examples.embodied.perception import CloudSemantics
        from roboagent.message import AssistantMessage
        from tests.support.models import ScriptedModel

        response = {
            "source": "camera",
            "observation_id": "1",
            "detections": [
                {
                    "local_id": "local-1",
                    "category": "cube",
                    "attributes": {"color": "red"},
                    "bbox": [0, 0, 2, 2],
                }
            ],
        }
        perception = CloudSemantics(
            ScriptedModel(AssistantMessage(json.dumps(response))),
            lambda observation, detection: EntityRef(
                "tracked:" + str(detection["local_id"])
            ),
        )
        from roboagent.message import TextContent

        result = await perception.detect(
            sample().observation, TextContent("fixture image")
        )
        assert result[0].entity == EntityRef("tracked:local-1")
        response["observation_id"] = "wrong"
        perception = CloudSemantics(
            ScriptedModel(AssistantMessage(json.dumps(response))),
            lambda observation, detection: EntityRef(str(detection["local_id"])),
        )
        from jsonschema import ValidationError

        with pytest.raises(ValidationError):
            await perception.detect(sample().observation, TextContent("fixture image"))

    asyncio.run(check())


@pytest.mark.parametrize("quaternion", [(1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)])
def test_full_pose_requires_object_origin_and_orientation_from_same_sample(quaternion):
    async def check():
        class Clock:
            def now(self, clock_id):
                return ObservationTime("camera", 10) if clock_id == "camera" else None

        w = World(
            "w",
            clock_resolver=Clock(),
            allowed_sources=("camera",),
            allowed_scopes=("workspace",),
        )

        async def transform(frame, time, point):
            return point

        async def measured_pose(captured, detection):
            assert captured.observation.observed_at == ObservationTime("camera", 10)
            assert detection.entity == EntityRef("cube")
            return {
                "xyz": [0.5, 0.5, 1.1],
                "quaternion": quaternion,
                "frame_id": "world",
            }

        adapter = RGBDAdapter(
            w,
            source="camera",
            scope="workspace",
            max_skew_ns=2,
            transform=transform,
            pose_from_sample=measured_pose,
        )
        detections = (
            Detection(EntityRef("cube"), "cube", {"color": "red"}, (0, 0, 2, 2)),
        )
        if quaternion[0] == 0:
            with pytest.raises(ValueError, match="invalid_measured_pose"):
                await adapter.commit(sample(), detections)
            assert (await w.snapshot()).revision == 0
        else:
            await adapter.commit(sample(), detections)
            pose = await w.query(EntityRef("cube"), "pose", scope="workspace")
            from roboagent.world import WorldStatus

            position = await w.query(EntityRef("cube"), "position", scope="workspace")
            assert tuple(position.value["xyz"]) == (0.5, 0.5, 1.0)
            assert (
                pose.status is WorldStatus.KNOWN
                and tuple(pose.value["quaternion"]) == quaternion
                and tuple(pose.value["xyz"]) == (0.5, 0.5, 1.1)
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "pose",
    [
        {"xyz": [0, 0, 1], "quaternion": [1, 0, 0, 0], "frame_id": "camera_frame"},
        {"xyz": [0, float("nan"), 1], "quaternion": [1, 0, 0, 0], "frame_id": "world"},
        {"xyz": [True, 0, 1], "quaternion": [1, 0, 0, 0], "frame_id": "world"},
        {"xyz": [0, 0], "quaternion": [1, 0, 0, 0], "frame_id": "world"},
        {"quaternion": [1, 0, 0, 0], "frame_id": "world"},
    ],
)
def test_invalid_full_object_pose_never_partially_commits(pose):
    async def check():
        w = World("w", allowed_sources=("camera",), allowed_scopes=("workspace",))

        async def transform(frame, time, point):
            return point

        async def measured_pose(captured, detection):
            return pose

        adapter = RGBDAdapter(
            w,
            source="camera",
            scope="workspace",
            max_skew_ns=2,
            transform=transform,
            pose_from_sample=measured_pose,
        )
        with pytest.raises(ValueError, match="invalid_measured_pose"):
            await adapter.commit(
                sample(), (Detection(EntityRef("cube"), "cube", {}, (0, 0, 2, 2)),)
            )
        assert (await w.snapshot()).revision == 0

    asyncio.run(check())
