"""Bounded direct visual interaction without persistent evidence identity."""

from .buffer import VisionBuffer
from .content import image_content_from_frame
from .types import VisionFrame

__all__ = ["VisionBuffer", "VisionFrame", "image_content_from_frame"]
