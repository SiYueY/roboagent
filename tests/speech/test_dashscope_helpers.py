"""Regression tests for shared DashScope adapter boundary helpers."""

from types import SimpleNamespace
import unittest

from roboagent.speech._dashscope import get_field


class DashScopeHelperTests(unittest.TestCase):
    def test_get_field_supports_sdk_mapping_and_attribute_shapes(self) -> None:
        self.assertEqual(get_field({"code": "ok"}, "code"), "ok")
        self.assertEqual(get_field(SimpleNamespace(code="ok"), "code"), "ok")

    def test_get_field_returns_default_only_for_missing_values(self) -> None:
        self.assertEqual(get_field({}, "code", "unknown"), "unknown")
        self.assertIsNone(get_field(SimpleNamespace(code=None), "code", "unknown"))
