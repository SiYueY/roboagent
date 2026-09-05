from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from roboagent.config import AppConfig, load_app_config
from roboagent.model.errors import ModelConfigError
from roboagent.skill import SkillConfig


class SkillConfigTests(unittest.TestCase):
    def test_skill_config_only_contains_guidance_bounds(self) -> None:
        self.assertEqual(SkillConfig(max_description_chars=12, max_body_bytes=34).max_body_bytes, 34)
        with self.assertRaises(ValueError):
            SkillConfig(max_body_bytes=0)


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
                "skills": {"max_body_bytes": 1234},
            }
        )

        registry = config.to_model_registry()
        skill_manager = config.create_skill_manager()

        self.assertEqual(registry.default_model, "openai-main")
        self.assertEqual(skill_manager.loader.config.max_body_bytes, 1234)

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
                        "  max_body_bytes: 1234",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ROBOAGENT_CONFIG_PATH": str(config_path)}, clear=False):
                config = load_app_config()

        self.assertEqual(config.default_model, "openai-main")
        self.assertEqual(config.skills.max_body_bytes, 1234)

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
            config_path.write_text("models: []\n", encoding="utf-8")
            (directory / ".env").write_text("ROBOAGENT_TEST_PATH=from-dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {"ROBOAGENT_TEST_PATH": "from-shell"}, clear=True):
                config = AppConfig.from_yaml(config_path)
            self.assertEqual(config.skills, SkillConfig())

    def test_missing_environment_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("models: []\ndefault_model: '${ROBOAGENT_MISSING_VALUE}'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ModelConfigError, "ROBOAGENT_MISSING_VALUE"):
                    AppConfig.from_yaml(config_path)


if __name__ == "__main__":
    unittest.main()
