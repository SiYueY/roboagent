import importlib.util

import pytest

from roboagent.interaction.vision import (
    VisionBuffer,
    VisionFrame,
    image_content_from_frame,
)
from roboagent.message import BytesSource, MediaLimits, ProtocolError


def frame(time=10, data=b"jpeg"):
    return VisionFrame(data, "image/jpeg", 1280, 720, time, "camera")


def test_temporal_retention_nearest_and_bytes():
    now = [12]
    buffer = VisionBuffer(max_frames=2, max_age=5, max_bytes=8, clock=lambda: now[0])
    buffer.push(frame(10))
    buffer.push(frame(11))
    assert buffer.nearest(10.2).captured_at == 10
    assert buffer.nearest(9, max_age=0.5) is None
    buffer.push(frame(12))
    assert buffer.nearest(10).captured_at == 11
    assert buffer.latest().captured_at == 12
    now[0] = 18
    assert buffer.latest() is None
    buffer.clear()
    assert buffer.nearest(12) is None
    with pytest.raises(ValueError):
        buffer.push(frame(18, b"x" * 9))


def test_conversion_validation_and_breaking_paths():
    content = image_content_from_frame(frame())
    assert content.source == BytesSource(b"jpeg") and content.media_type == "image/jpeg"
    assert not hasattr(frame(), "id") and not hasattr(frame(), "timestamp")
    with pytest.raises(ProtocolError):
        image_content_from_frame(frame(), limits=MediaLimits(max_inline_bytes=2))
    for kwargs in (
        {"mime_type": "text/plain"},
        {"width": 0},
        {"data": b""},
        {"captured_at": float("nan")},
    ):
        params = dict(data=b"jpeg", mime_type="image/jpeg", width=1, height=1)
        params.update(kwargs)
        with pytest.raises(ProtocolError):
            VisionFrame(**params)
    for old in ("roboagent." + "vision", "roboagent." + "speech"):
        assert importlib.util.find_spec(old) is None


def test_host_adapter_creates_separate_world_evidence_identity_and_clock():
    import asyncio
    from datetime import datetime, timezone
    from uuid import uuid4
    from roboagent.world import (
        Observation,
        ObservationRef,
        ObservationTime,
        ResourceRef,
        World,
    )

    async def check():
        direct = frame()
        w = World("camera-world", allowed_sources=("host/camera",))
        # Host owns the media resource and assigns provenance. Interaction
        # source strings and timestamps are not sensor credentials or ROS Time.
        ref = ObservationRef("host/camera", uuid4().hex)
        sampled = ObservationTime(
            "host-monotonic:test-boot", int(direct.captured_at * 1e9)
        )
        evidence = Observation(
            ref,
            "image",
            sampled,
            datetime.now(timezone.utc),
            ResourceRef("memory://host/frame", direct.mime_type, len(direct.data)),
        )
        receipt = await w.observe(evidence)
        assert receipt.observation_ref == ref
        assert ref.source != direct.source and not hasattr(direct, "id")
        assert (await w.snapshot()).entries == ()  # pixels do not become semantic truth

    asyncio.run(check())


def test_late_frame_does_not_displace_newer_retained_frame():
    buffer = VisionBuffer(max_frames=1, clock=lambda: 12)
    buffer.push(frame(12))
    buffer.push(frame(10))
    assert buffer.latest().captured_at == 12
