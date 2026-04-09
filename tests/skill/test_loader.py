from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roboagent.skill import SkillLoader


class SkillLoaderTests(unittest.TestCase):
    def test_load_source_discovers_and_builds_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            skill_dir = source / "nav-plan"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: nav-plan\n"
                    "description: Generate navigation plans.\n"
                    "version: 1.2.3\n"
                    "trigger-keywords: navigate,waypoint\n"
                    "allowed-tools: map.read pose.read\n"
                    "required-permissions: tool:map.read tool:pose.read\n"
                    "entrypoint: roboagent.skills.handlers.nav:run\n"
                    "---\n\n"
                    "Use the navigation pipeline.\n"
                ),
                encoding="utf-8",
            )

            loader = SkillLoader()
            skills = loader.load_source(source)

            self.assertEqual(len(skills), 1)
            skill = skills[0]
            self.assertEqual(skill.name, "nav-plan")
            self.assertEqual(skill.source, "builtin")
            self.assertEqual(skill.version, "1.2.3")
            self.assertEqual(skill.body, "Use the navigation pipeline.")
            self.assertEqual(skill.trigger_keywords, ("navigate", "waypoint"))
            self.assertEqual(skill.allowed_tools, ("map.read", "pose.read"))

    def test_load_skill_file_warns_but_loads_for_directory_name_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            skill_dir = source / "wrong-dir"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                (
                    "---\n"
                    "name: nav-plan\n"
                    "description: Generate navigation plans.\n"
                    "---\n\n"
                    "Use the navigation pipeline.\n"
                ),
                encoding="utf-8",
            )

            loader = SkillLoader()
            skill = loader.load_skill_file(skill_file, source=source)

            self.assertEqual(skill.name, "nav-plan")
            self.assertEqual(skill.source_dir, skill_dir)

    def test_load_source_skips_invalid_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            valid_dir = source / "nav-plan"
            invalid_dir = source / "broken-skill"
            valid_dir.mkdir(parents=True)
            invalid_dir.mkdir(parents=True)

            (valid_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: nav-plan\n"
                    "description: Generate navigation plans.\n"
                    "---\n\n"
                    "Use the navigation pipeline.\n"
                ),
                encoding="utf-8",
            )
            (invalid_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: broken-skill\n"
                    "version: not-semver\n"
                    "---\n\n"
                    "Broken skill.\n"
                ),
                encoding="utf-8",
            )

            loader = SkillLoader()
            skills = loader.load_source(source)

            self.assertEqual([skill.name for skill in skills], ["nav-plan"])

    def test_load_all_uses_configured_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            builtin = Path(tmp_dir) / "builtin"
            custom = Path(tmp_dir) / "custom"
            for source, skill_name in ((builtin, "nav-plan"), (custom, "arm-control")):
                skill_dir = source / skill_name
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(
                    (
                        "---\n"
                        f"name: {skill_name}\n"
                        f"description: Skill for {skill_name}.\n"
                        "---\n\n"
                        f"Run {skill_name}.\n"
                    ),
                    encoding="utf-8",
                )

            loader = SkillLoader(sources=[builtin, custom])
            skills = loader.load_all()

            self.assertEqual([skill.name for skill in skills], ["nav-plan", "arm-control"])
            self.assertEqual([skill.source for skill in skills], ["builtin", "custom"])

    def test_discover_skill_files_skips_ignored_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "builtin"
            valid_dir = source / "nav-plan"
            ignored_dir = source / "node_modules" / "bad-skill"
            valid_dir.mkdir(parents=True)
            ignored_dir.mkdir(parents=True)
            (valid_dir / "SKILL.md").write_text(
                "---\nname: nav-plan\ndescription: Valid.\n---\n",
                encoding="utf-8",
            )
            (ignored_dir / "SKILL.md").write_text(
                "---\nname: bad-skill\ndescription: Ignored.\n---\n",
                encoding="utf-8",
            )

            loader = SkillLoader()
            skill_files = loader.discover_skill_files(source)

            self.assertEqual(skill_files, [valid_dir / "SKILL.md"])


if __name__ == "__main__":
    unittest.main()
