from __future__ import annotations

import unittest

from pydantic import ValidationError

from roboagent.tool import ToolSpec
from roboagent.tool.errors import ToolRegistrationError


class ToolSpecTests(unittest.TestCase):
    def test_minimal_spec_is_valid(self) -> None:
        spec = ToolSpec(
            name="map.read",
            description="Read map data.",
            group="map",
            source="builtin",
        )

        self.assertEqual(spec.name, "map.read")
        self.assertEqual(spec.group, "map")
        self.assertTrue(spec.visible_by_default)
        self.assertFalse(spec.deferred)
        self.assertEqual(spec.allowed_agents, ())

    def test_allowed_agents_are_normalized_and_deduplicated(self) -> None:
        spec = ToolSpec(
            name="map.read",
            description="Read map data.",
            group="map",
            source="builtin",
            allowed_agents="lead worker lead",
        )

        self.assertEqual(spec.allowed_agents, ("lead", "worker"))

    def test_empty_required_fields_raise_registration_error(self) -> None:
        with self.assertRaises(ToolRegistrationError):
            ToolSpec(
                name="",
                description="Read map data.",
                group="map",
                source="builtin",
            )

    def test_invalid_allowed_agents_shape_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ToolSpec(
                name="map.read",
                description="Read map data.",
                group="map",
                source="builtin",
                allowed_agents=123,
            )


if __name__ == "__main__":
    unittest.main()
