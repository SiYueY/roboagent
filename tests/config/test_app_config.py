from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from roboagent.config import AppConfig, SkillConfig, SubagentConfig, load_app_config
from roboagent.model.errors import ModelConfigError


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

    def test_loads_sibling_dotenv_and_expands_nested_model_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            config_path = directory / "config.yaml"
            config_path.write_text(
                "models:\n  - name: openai-main\n    provider: openai\n    params:\n      model: gpt-4o-mini\n      api_key: ${ROBOAGENT_TEST_API_KEY}\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text("ROBOAGENT_TEST_API_KEY=from-dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = AppConfig.from_yaml(config_path)
            self.assertEqual(config.models[0].params.api_key, "from-dotenv")

    def test_shell_environment_overrides_sibling_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            config_path = directory / "config.yaml"
            config_path.write_text("models: []\nskills:\n  sources: ['${ROBOAGENT_TEST_PATH}']\n", encoding="utf-8")
            (directory / ".env").write_text("ROBOAGENT_TEST_PATH=from-dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {"ROBOAGENT_TEST_PATH": "from-shell"}, clear=True):
                config = AppConfig.from_yaml(config_path)
            self.assertEqual(config.skills.sources, (Path("from-shell"),))

    def test_missing_environment_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("models: []\nskills:\n  sources: ['${ROBOAGENT_MISSING_VALUE}']\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ModelConfigError, "ROBOAGENT_MISSING_VALUE"):
                    AppConfig.from_yaml(config_path)


if __name__ == "__main__":
    unittest.main()
