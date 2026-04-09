from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from roboagent.skill import Skill, SkillSpec


class SkillSpecTests(unittest.TestCase):
    def test_minimal_skill_spec_is_valid(self) -> None:
        spec = SkillSpec(name="nav-plan", description="Generate navigation plans.")

        self.assertEqual(spec.name, "nav-plan")
        self.assertEqual(spec.version, "0.1.0")
        self.assertFalse(spec.is_executable)

    def test_invalid_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(name="Nav Plan", description="Invalid skill name.")

    def test_missing_description_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(name="nav-plan")

    def test_string_sequences_are_normalized(self) -> None:
        spec = SkillSpec(
            name="nav-plan",
            description="Generate navigation plans.",
            version="1.2.3",
            trigger_keywords="navigate, path planning, navigate",
            allowed_tools="map.read pose.read map.read",
            required_permissions="tool:map.read tool:pose.read tool:map.read",
        )

        self.assertEqual(spec.version, "1.2.3")
        self.assertEqual(spec.trigger_keywords, ("navigate", "path planning"))
        self.assertEqual(spec.allowed_tools, ("map.read", "pose.read"))
        self.assertEqual(spec.required_permissions, ("tool:map.read", "tool:pose.read"))

    def test_invalid_extension_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(
                name="nav-plan",
                description="Generate navigation plans.",
                version="not-semver",
            )

        with self.assertRaises(ValidationError):
            SkillSpec(
                name="nav-plan",
                description="Generate navigation plans.",
                entrypoint="./handlers/nav.py",
            )

    def test_metadata_validation_errors_are_wrapped_by_pydantic(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(name="nav-plan", description="Generate navigation plans.", metadata="oops")

    def test_from_frontmatter_supports_kebab_case_aliases(self) -> None:
        spec = SkillSpec.from_frontmatter(
            {
                "name": "nav-plan",
                "description": "Generate navigation plans.",
                "license": "MIT",
                "trigger-keywords": "navigate,waypoint",
                "allowed-tools": "map.read pose.read",
                "required-permissions": "tool:map.read tool:pose.read",
                "entrypoint": "roboagent.skills.handlers.nav:run",
                "metadata": {"domain": "robotics", "priority": 1, "tags": "robotics,navigation"},
            },
            body="Use the navigation pipeline.",
        )

        self.assertEqual(spec.license, "MIT")
        self.assertEqual(spec.trigger_keywords, ("navigate", "waypoint"))
        self.assertEqual(spec.tags, ("robotics", "navigation"))
        self.assertEqual(spec.allowed_tools, ("map.read", "pose.read"))
        self.assertEqual(spec.required_permissions, ("tool:map.read", "tool:pose.read"))
        self.assertEqual(spec.entrypoint, "roboagent.skills.handlers.nav:run")
        self.assertEqual(
            spec.metadata,
            {
                "domain": "robotics",
                "priority": "1",
                "tags": "robotics,navigation",
                "trigger-keywords": "navigate,waypoint",
                "required-permissions": "tool:map.read tool:pose.read",
                "entrypoint": "roboagent.skills.handlers.nav:run",
            },
        )
        self.assertEqual(spec.body, "Use the navigation pipeline.")

    def test_official_frontmatter_shape_is_preserved(self) -> None:
        spec = SkillSpec(
            name="nav-plan",
            description="Generate navigation plans.",
            license="MIT",
            compatibility="roboagent>=0.1",
            version="1.0.0",
            trigger_keywords="navigate,waypoint",
        )

        self.assertEqual(
            spec.to_frontmatter_dict(),
            {
                "name": "nav-plan",
                "description": "Generate navigation plans.",
                "allowed-tools": "",
                "metadata": {"version": "1.0.0", "trigger-keywords": "navigate,waypoint"},
                "license": "MIT",
                "compatibility": "roboagent>=0.1",
            },
        )


class SkillRuntimeTests(unittest.TestCase):
    def test_skill_is_independent_from_skill_spec(self) -> None:
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
            version="0.1.0",
            trigger_keywords=("navigate", "waypoint"),
            allowed_tools=("map.read", "pose.read"),
            required_permissions=("tool:map.read", "tool:pose.read"),
            entrypoint="roboagent.skills.handlers.nav:run",
            skill_file=Path("/skills/builtin/nav-plan/SKILL.md"),
            enabled=False,
        )

        self.assertEqual(skill.name, "nav-plan")
        self.assertTrue(skill.is_executable)
        self.assertEqual(skill.identity, "nav-plan@0.1.0")
        self.assertFalse(skill.enabled)
        self.assertEqual(
            skill.to_dict(),
            {
                "name": "nav-plan",
                "description": "Generate navigation plans.",
                "license": None,
                "compatibility": None,
                "version": "0.1.0",
                "body": "",
                "trigger_keywords": ("navigate", "waypoint"),
                "tags": (),
                "allowed_tools": ("map.read", "pose.read"),
                "required_permissions": ("tool:map.read", "tool:pose.read"),
                "entrypoint": "roboagent.skills.handlers.nav:run",
                "metadata": {},
                "source": "builtin",
                "source_dir": "/skills/builtin/nav-plan",
                "skill_file": "/skills/builtin/nav-plan/SKILL.md",
                "enabled": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
