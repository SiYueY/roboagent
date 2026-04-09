from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roboagent.skill import Skill, SkillLoader, SkillManager


class SkillManagerTests(unittest.TestCase):
    def test_load_and_select(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            skill_dir = source / "nav-plan"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: nav-plan\n"
                    "description: Generate navigation plans for robot movement.\n"
                    "trigger-keywords: navigate,waypoint\n"
                    "---\n\n"
                    "Use the navigation pipeline.\n"
                ),
                encoding="utf-8",
            )

            manager = SkillManager(sources=[source])
            manager.load()
            matches = manager.select("navigate through these waypoints", top_k=1)

            self.assertEqual([skill.name for skill in matches], ["nav-plan"])

    def test_disable_and_enable_skill(self) -> None:
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )
        manager = SkillManager()
        manager.register(skill)

        disabled = manager.disable("nav-plan")
        enabled = manager.enable("nav-plan")

        self.assertFalse(disabled.enabled)
        self.assertTrue(enabled.enabled)
        self.assertTrue(manager.is_enabled("nav-plan"))

    def test_has_skill_and_is_enabled(self) -> None:
        skill = Skill(
            name="nav-plan",
            description="Generate navigation plans.",
            source="builtin",
            source_dir=Path("/skills/builtin/nav-plan"),
        )
        manager = SkillManager()
        manager.register(skill)

        self.assertTrue(manager.has_skill("nav-plan"))
        self.assertTrue(manager.is_enabled("nav-plan"))
        self.assertFalse(manager.has_skill("missing-skill"))
        self.assertFalse(manager.is_enabled("missing-skill"))

    def test_disable_changes_enabled_status(self) -> None:
        skill = Skill(
            name="arm-control",
            description="Control the robot arm.",
            source="builtin",
            source_dir=Path("/skills/builtin/arm-control"),
        )
        manager = SkillManager()
        manager.register(skill)
        manager.disable("arm-control")

        self.assertFalse(manager.is_enabled("arm-control"))

    def test_injected_loader_sources_are_used_by_default(self) -> None:
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

            manager = SkillManager(loader=SkillLoader(sources=[source]))
            loaded = manager.load()

            self.assertEqual([skill.name for skill in loaded], ["nav-plan"])


if __name__ == "__main__":
    unittest.main()
