from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from roboagent.config import AppConfig, SkillConfig, SubagentConfig, load_app_config


class SkillConfigTests(unittest.TestCase):
    def test_skill_config_normalizes_sources_and_permissions(self) -> None:
        config = SkillConfig(
            sources=["./skills", "~/custom-skills"],
            disabled_skills="old-skill",
            allowed_permissions="tool:file.read tool:map.read tool:file.read",
        )

        self.assertEqual(config.sources[0], Path("skills"))
        self.assertEqual(config.disabled_skills, ("old-skill",))
        self.assertEqual(config.allowed_permissions, ("tool:file.read", "tool:map.read"))

    def test_skill_config_rejects_conflicting_toggles(self) -> None:
        with self.assertRaises(ValidationError):
            SkillConfig(enabled_skills=["nav-plan"], disabled_skills=["nav-plan"])


class SubagentConfigTests(unittest.TestCase):
    def test_subagent_config_normalizes_allowed_lists(self) -> None:
        config = SubagentConfig(id="planner", role="planning", allowed_tools="map.read pose.read")

        self.assertEqual(config.allowed_tools, ("map.read", "pose.read"))
        self.assertTrue(config.enabled)


class AppConfigTests(unittest.TestCase):
    def test_app_config_builds_model_registry_and_skill_manager(self) -> None:
        config = AppConfig.from_dict(
            {
                "default_model": "openai-main",
                "models": [
                    {
                        "name": "openai-main",
                        "provider": "openai",
                        "params": {"model": "gpt-4o-mini"},
                    }
                ],
                "skills": {"sources": ["./skills"], "allowed_permissions": "tool:file.read"},
                "subagents": [{"id": "planner", "allowed_skills": "nav-plan"}],
            }
        )

        registry = config.to_model_registry()
        skill_manager = config.create_skill_manager()

        self.assertEqual(registry.default_model, "openai-main")
        self.assertEqual(skill_manager.registry.loader.sources, (Path("skills"),))
        self.assertEqual(config.subagents[0].allowed_skills, ("nav-plan",))

    def test_app_config_rejects_duplicate_subagents(self) -> None:
        with self.assertRaises(ValidationError):
            AppConfig.from_dict({"subagents": [{"id": "planner"}, {"id": "planner"}]})

    def test_load_app_config_uses_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "default_model: openai-main",
                        "models:",
                        "  - name: openai-main",
                        "    provider: openai",
                        "    params:",
                        "      model: gpt-4o-mini",
                        "skills:",
                        "  sources:",
                        "    - ./skills",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ROBOAGENT_CONFIG_PATH": str(config_path)}, clear=False):
                config = load_app_config()

        self.assertEqual(config.default_model, "openai-main")
        self.assertEqual(config.skills.sources, (Path("skills"),))


if __name__ == "__main__":
    unittest.main()
