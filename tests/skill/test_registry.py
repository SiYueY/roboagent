from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roboagent.skill import DuplicateSkillError, Skill, SkillNotFoundError, SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    def test_register_and_get_skill(self) -> None:
        registry = SkillRegistry()
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )

        registry.register(skill)

        self.assertTrue(registry.has("nav-plan"))
        self.assertEqual(registry.get("nav-plan"), skill)
        self.assertEqual(registry.require("nav-plan"), skill)

    def test_register_without_replace_raises_duplicate(self) -> None:
        registry = SkillRegistry()
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )
        registry.register(skill)

        with self.assertRaises(DuplicateSkillError):
            registry.register(skill, replace=False)

    def test_unregister_missing_skill_can_raise(self) -> None:
        registry = SkillRegistry()

        with self.assertRaises(SkillNotFoundError):
            registry.unregister("missing-skill", missing_ok=False)

    def test_list_enabled_filters_disabled_skills(self) -> None:
        enabled = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )
        disabled = Skill(
            name="arm-control",
            description="Control the robot arm.",
            source="builtin",
            source_dir=Path("/skills/builtin/arm-control"),
            enabled=False,
        )
        registry = SkillRegistry([enabled, disabled])

        self.assertEqual([skill.name for skill in registry.list_enabled()], ["nav-plan"])

    def test_match_prioritizes_name_and_keywords(self) -> None:
        registry = SkillRegistry(
            [
                Skill(
                    name="nav-plan",
                    description="Generate navigation plans for robot movement.",
                    source="builtin",
                    source_dir=Path("/skills/builtin/nav-plan"),
                    trigger_keywords=("navigate", "waypoint"),
                    tags=("robotics", "navigation"),
                ),
                Skill(
                    name="arm-control",
                    description="Control the robot arm and joints.",
                    source="builtin",
                    source_dir=Path("/skills/builtin/arm-control"),
                    trigger_keywords=("grasp", "joint"),
                    tags=("robotics", "manipulation"),
                ),
            ]
        )

        matches = registry.match("Need a nav-plan skill to navigate waypoints", top_k=2)

        self.assertEqual([skill.name for skill in matches], ["nav-plan", "arm-control"][: len(matches)])
        self.assertEqual(matches[0].name, "nav-plan")

    def test_load_source_registers_loader_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            skill_dir = source / "nav-plan"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: nav-plan\n"
                    "description: Generate navigation plans.\n"
                    "---\n\n"
                    "Use the navigation pipeline.\n"
                ),
                encoding="utf-8",
            )

            registry = SkillRegistry()
            loaded = registry.load_source(source)

            self.assertEqual([skill.name for skill in loaded], ["nav-plan"])
            self.assertEqual(registry.count(), 1)
            self.assertEqual(registry.require("nav-plan").source, "builtin")

    def test_register_many_is_atomic_when_replace_is_false(self) -> None:
        existing = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )
        new_skill = Skill(
            name="arm-control",
            description="Control the robot arm.",
            source="builtin",
            source_dir=Path("/skills/builtin/arm-control"),
        )
        registry = SkillRegistry([existing])

        with self.assertRaises(DuplicateSkillError):
            registry.register_many([new_skill, existing], replace=False)

        self.assertTrue(registry.has("nav-plan"))
        self.assertFalse(registry.has("arm-control"))


if __name__ == "__main__":
    unittest.main()
