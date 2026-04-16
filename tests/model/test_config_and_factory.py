from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from roboagent.config import ModelsAppConfig, get_model_registry, reload_model_registry, reset_model_registry
from roboagent.model.factory import create_chat_model


class ModelsAppConfigTests(unittest.TestCase):
    def test_from_dict_reads_model_keys_from_app_payload(self) -> None:
        payload = {
            "log_level": "info",
            "default_model": "openai-main",
            "models": [
                {
                    "name": "openai-main",
                    "display_name": "OpenAI Main",
                    "provider": "openai",
                    "params": {"model": "gpt-4o-mini"},
                }
            ],
        }

        config = ModelsAppConfig.from_dict(payload)

        self.assertEqual(config.default_model, "openai-main")
        self.assertEqual(config.models[0].name, "openai-main")

    def test_to_registry_preserves_default_model(self) -> None:
        config = ModelsAppConfig.from_dict(
            {
                "default_model": "deepseek-main",
                "models": [
                    {
                        "name": "deepseek-main",
                        "display_name": "DeepSeek",
                        "provider": "deepseek",
                        "params": {"model": "deepseek-chat"},
                    }
                ],
            }
        )

        registry = config.to_registry()

        self.assertEqual(registry.default_model, "deepseek-main")


class ModelConfigRegistryLoaderTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_model_registry()

    def test_get_model_registry_loads_config_file_and_registers_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "default_model: openai-main",
                        "models:",
                        "  - name: openai-main",
                        "    display_name: OpenAI Main",
                        "    provider: openai",
                        "    params:",
                        "      model: gpt-4o-mini",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            registry = get_model_registry(config_path)

        self.assertEqual(registry.default_model, "openai-main")
        self.assertTrue(registry.has("openai-main"))

    def test_get_model_registry_uses_env_path_when_no_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "default_model: deepseek-main",
                        "models:",
                        "  - name: deepseek-main",
                        "    display_name: DeepSeek Main",
                        "    provider: deepseek",
                        "    params:",
                        "      model: deepseek-chat",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ROBOAGENT_CONFIG_PATH": str(config_path)}, clear=False):
                registry = reload_model_registry()

        self.assertEqual(registry.default_model, "deepseek-main")
        self.assertTrue(registry.has("deepseek-main"))


class ModelFactoryTests(unittest.TestCase):
    def test_create_chat_model_routes_openai_config(self) -> None:
        registry = ModelsAppConfig.from_dict(
            {
                "default_model": "openai-main",
                "models": [
                    {
                        "name": "openai-main",
                        "display_name": "OpenAI Main",
                        "provider": "openai",
                        "params": {"model": "gpt-4o-mini"},
                    }
                ],
            }
        ).to_registry()

        sentinel = object()
        with (
            patch("roboagent.model.factory.get_model_registry", return_value=registry),
            patch("roboagent.model.factory.create_openai_chat_model", return_value=sentinel) as mock_create,
        ):
            result = create_chat_model(name="openai-main", temperature=0.1)

        self.assertIs(result, sentinel)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["temperature"], 0.1)

    def test_create_chat_model_routes_deepseek_config(self) -> None:
        registry = ModelsAppConfig.from_dict(
            {
                "default_model": "deepseek-main",
                "models": [
                    {
                        "name": "deepseek-main",
                        "display_name": "DeepSeek Main",
                        "provider": "deepseek",
                        "params": {"model": "deepseek-chat"},
                    }
                ],
            }
        ).to_registry()

        sentinel = object()
        with (
            patch("roboagent.model.factory.get_model_registry", return_value=registry),
            patch("roboagent.model.factory.create_deepseek_chat_model", return_value=sentinel),
        ):
            result = create_chat_model(name="deepseek-main")

        self.assertIs(result, sentinel)

    def test_create_chat_model_routes_tongyi_config(self) -> None:
        registry = ModelsAppConfig.from_dict(
            {
                "default_model": "qwen-main",
                "models": [
                    {
                        "name": "qwen-main",
                        "display_name": "Qwen Main",
                        "provider": "tongyi",
                        "params": {"model": "qwen-max"},
                    }
                ],
            }
        ).to_registry()

        sentinel = object()
        with (
            patch("roboagent.model.factory.get_model_registry", return_value=registry),
            patch("roboagent.model.factory.create_tongyi_chat_model", return_value=sentinel),
        ):
            result = create_chat_model(name="qwen-main")

        self.assertIs(result, sentinel)

    def test_create_chat_model_uses_default_name_resolution(self) -> None:
        config = ModelsAppConfig.from_dict(
            {
                "default_model": "openai-main",
                "models": [
                    {
                        "name": "openai-main",
                        "display_name": "OpenAI",
                        "provider": "openai",
                        "params": {"model": "gpt-4o-mini"},
                    },
                    {
                        "name": "deepseek-main",
                        "display_name": "DeepSeek",
                        "provider": "deepseek",
                        "params": {"model": "deepseek-chat"},
                    },
                ],
            }
        )
        registry = config.to_registry()

        sentinel = object()
        with (
            patch("roboagent.model.factory.get_model_registry", return_value=registry),
            patch("roboagent.model.factory.create_openai_chat_model", return_value=sentinel),
        ):
            result = create_chat_model()

        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
