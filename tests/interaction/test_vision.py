"""Direct visual interaction shares the new bounded capture primitive."""

from dataclasses import replace

import pytest

from roboagent.interaction.vision import (
    CaptureTime,
    VisualBuffer,
    VisionLimits,
    VisionError,
    image_content,
)
from tests.vision.support import capture


def test_buffer_temporal_retention_nearest_and_late_input():
    now = [3_000_000]
    buffer = VisualBuffer(
        stream_id="camera",
        clock=lambda domain: CaptureTime(domain, now[0]),
        limits=VisionLimits(max_buffer_frames=2, max_buffer_age_ns=5_000_000),
    )
    buffer.activate_epoch("clock")
    first = capture(sequence=1)
    second = capture(sequence=2)
    third = capture(sequence=3)
    buffer.push(first)
    buffer.push(second)
    assert buffer.nearest(CaptureTime("clock", 1_100_000)).ref == first.ref
    buffer.push(third)
    buffer.push(first)
    assert buffer.latest().ref == third.ref
    assert buffer.nearest(CaptureTime("clock", 1_100_000), max_delta_ns=1000) is None
    now[0] = 10_000_000
    assert buffer.latest() is None


def test_buffer_binding_content_limits_and_conversion():
    sample = capture()
    buffer = VisualBuffer(
        stream_id="camera", clock=lambda domain: CaptureTime(domain, 0)
    )
    buffer.activate_epoch("clock")
    buffer.push(sample)
    assert image_content(sample.image).source.data == sample.image.data
    with pytest.raises(VisionError, match="capture_content_conflict"):
        buffer.push(replace(sample, capture_id="other"))
    buffer.activate_epoch("new")
    with pytest.raises(VisionError, match="buffer_epoch_mismatch"):
        buffer.push(sample)
    with pytest.raises(VisionError, match="buffer_epoch_mismatch"):
        buffer.nearest(sample.captured_at)


def test_deleted_legacy_interfaces_and_core_only_import():
    import subprocess
    import sys
    import roboagent.interaction.vision as vision

    assert not hasattr(vision, "VisionFrame") and not hasattr(vision, "VisionBuffer")
    source = """
import sys
class BlockCPU:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('numpy', 'cv2'):
            raise ImportError('CPU extras absent')
sys.meta_path.insert(0, BlockCPU())
import roboagent
from roboagent.interaction.vision import ImageFrame, VisionError
try:
    ImageFrame(bytes.fromhex('89504e470d0a1a0a0000000d494844520000000100000001'),'image/png',1,1)
except VisionError as error:
    assert error.code == 'vision_dependencies_unavailable'
else:
    raise AssertionError('dependency failure expected')
"""
    subprocess.run([sys.executable, "-c", source], check=True, timeout=15)
