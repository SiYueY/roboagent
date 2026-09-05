from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from roboagent.config import ModelsAppConfig, get_model_registry, reload_model_registry, reset_model_registry
from roboagent.model.errors import ModelProviderError
from roboagent.model.factory import ConfiguredModelProvider, create_model


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

    def test_from_yaml_uses_the_shared_environment_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            config_path = directory / "config.yaml"
            config_path.write_text(
                "models:\n  - name: openai-main\n    provider: openai\n    params:\n      model: ${ROBOAGENT_TEST_MODEL}\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text("ROBOAGENT_TEST_MODEL=gpt-4o-mini\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = ModelsAppConfig.from_yaml(config_path)
            self.assertEqual(config.models[0].params.model, "gpt-4o-mini")


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
    def test_create_model_routes_openai_config(self) -> None:
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
            patch("roboagent.model.factory.create_openai_model", return_value=sentinel) as mock_create,
        ):
            result = create_model(name="openai-main", registry=registry, temperature=0.1)

        self.assertIs(result, sentinel)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["temperature"], 0.1)

    def test_create_model_routes_deepseek_config(self) -> None:
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
            patch("roboagent.model.factory.create_deepseek_model", return_value=sentinel),
        ):
            result = create_model(name="deepseek-main", registry=registry)

        self.assertIs(result, sentinel)

    def test_create_model_routes_tongyi_config(self) -> None:
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
            patch("roboagent.model.factory.create_tongyi_model", return_value=sentinel),
        ):
            result = create_model(name="qwen-main", registry=registry)

        self.assertIs(result, sentinel)

    def test_create_model_uses_default_name_resolution(self) -> None:
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
            patch("roboagent.model.factory.create_openai_model", return_value=sentinel),
        ):
            result = create_model(registry=registry)

        self.assertIs(result, sentinel)

    def test_create_model_requires_explicit_registry(self) -> None:
        with self.assertRaises(TypeError):
            create_model()

    def test_unknown_model_is_a_provider_error(self) -> None:
        registry = ModelsAppConfig.from_dict({"models": []}).to_registry()
        with self.assertRaises(ModelProviderError) as caught:
            create_model("missing", registry=registry)
        self.assertEqual(caught.exception.code, "model_resolution_failure")

    def test_configured_provider_caches_models_and_closes_shared_client_once(self) -> None:
        registry = ModelsAppConfig.from_dict(
            {
                "models": [
                    {"name": "one", "provider": "openai", "params": {"model": "model-one", "api_key": "test"}},
                    {"name": "two", "provider": "openai", "params": {"model": "model-two", "api_key": "test"}},
                ]
            }
        ).to_registry()

        class Client:
            closed = 0

            async def close(self) -> None:
                self.closed += 1

        client = Client()

        async def check() -> None:
            with patch("openai.AsyncOpenAI", return_value=client) as constructor:
                provider = ConfiguredModelProvider(registry)
                first = provider.get_model("one")
                assert provider.get_model("one") is first
                second = provider.get_model("two")
                assert first.client is second.client is client
                constructor.assert_called_once()
                await provider.close()
                await provider.close()
                assert client.closed == 1
                with self.assertRaises(ModelProviderError):
                    provider.get_model("one")

        import asyncio

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
