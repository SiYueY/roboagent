from __future__ import annotations

import unittest

from pydantic import TypeAdapter

import roboagent.model.providers as providers
from roboagent.model.providers import (
    DeepSeekModelConfig,
    OpenAIModelConfig,
    ProviderModelConfig,
    TongyiModelConfig,
)


class ProviderModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._adapter = TypeAdapter(ProviderModelConfig)

    def test_discriminator_parses_openai_variant(self) -> None:
        payload = {
            "name": "openai-main",
            "display_name": "OpenAI Main",
            "provider": "openai",
            "params": {
                "model": "gpt-4o-mini",
            },
        }

        parsed = self._adapter.validate_python(payload)

        self.assertIsInstance(parsed, OpenAIModelConfig)
        self.assertEqual(parsed.params.model, "gpt-4o-mini")

    def test_discriminator_parses_deepseek_variant(self) -> None:
        payload = {
            "name": "deepseek-main",
            "display_name": "DeepSeek Main",
            "provider": "deepseek",
            "params": {
                "model": "deepseek-chat",
            },
        }

        parsed = self._adapter.validate_python(payload)

        self.assertIsInstance(parsed, DeepSeekModelConfig)
        self.assertEqual(parsed.params.model, "deepseek-chat")

    def test_discriminator_parses_tongyi_variant(self) -> None:
        payload = {
            "name": "qwen-main",
            "display_name": "Qwen Main",
            "provider": "tongyi",
            "params": {
                "model": "qwen-max",
            },
        }

        parsed = self._adapter.validate_python(payload)

        self.assertIsInstance(parsed, TongyiModelConfig)
        self.assertEqual(parsed.params.model, "qwen-max")

    def test_public_exports_do_not_include_legacy_union_name(self) -> None:
        self.assertFalse(hasattr(providers, "AnyProviderModelConfig"))


if __name__ == "__main__":
    unittest.main()
