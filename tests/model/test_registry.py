from __future__ import annotations

import unittest

from roboagent.model.errors import DuplicateModelError, ModelConfigError, ModelNotFoundError
from roboagent.model.providers import DeepSeekModelConfig, OpenAIModelConfig
from roboagent.model.registry import ModelRegistry


def build_openai(name: str, *, enabled: bool = True) -> OpenAIModelConfig:
    """Build an OpenAI model config for registry tests."""
    return OpenAIModelConfig(
        name=name,
        display_name=name,
        provider="openai",
        enabled=enabled,
        params={"model": "gpt-4o-mini"},
    )


def build_deepseek(name: str, *, enabled: bool = True) -> DeepSeekModelConfig:
    """Build a DeepSeek model config for registry tests."""
    return DeepSeekModelConfig(
        name=name,
        display_name=name,
        provider="deepseek",
        enabled=enabled,
        params={"model": "deepseek-chat"},
    )


class ModelRegistryTests(unittest.TestCase):
    def test_resolve_by_explicit_name(self) -> None:
        openai = build_openai("openai-main")
        deepseek = build_deepseek("deepseek-main")
        registry = ModelRegistry([openai, deepseek], default_model="openai-main")

        resolved = registry.resolve("deepseek-main")

        self.assertEqual(resolved.name, "deepseek-main")

    def test_resolve_uses_default_when_name_is_missing(self) -> None:
        openai = build_openai("openai-main")
        deepseek = build_deepseek("deepseek-main")
        registry = ModelRegistry([openai, deepseek], default_model="deepseek-main")

        resolved = registry.resolve()

        self.assertEqual(resolved.name, "deepseek-main")

    def test_resolve_without_default_falls_back_to_first_enabled(self) -> None:
        openai = build_openai("openai-main", enabled=False)
        deepseek = build_deepseek("deepseek-main", enabled=True)
        registry = ModelRegistry([openai, deepseek])

        resolved = registry.resolve()

        self.assertEqual(resolved.name, "deepseek-main")

    def test_constructor_validates_default_model_presence(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelRegistry([build_openai("openai-main")], default_model="missing")

    def test_resolve_missing_model_raises(self) -> None:
        registry = ModelRegistry([build_openai("openai-main")])

        with self.assertRaises(ModelNotFoundError):
            registry.resolve("missing")

    def test_register_duplicate_model_name_raises(self) -> None:
        registry = ModelRegistry([build_openai("openai-main")])

        with self.assertRaises(DuplicateModelError):
            registry.register(build_openai("openai-main"))

    def test_batch_register_is_atomic_when_duplicates_exist(self) -> None:
        existing = build_openai("openai-main")
        new_model = build_deepseek("deepseek-main")
        registry = ModelRegistry([existing])

        with self.assertRaises(DuplicateModelError):
            registry.register_batch([new_model, existing])

        self.assertTrue(registry.has("openai-main"))
        self.assertFalse(registry.has("deepseek-main"))

    def test_constructor_with_duplicate_names_raises(self) -> None:
        with self.assertRaises(DuplicateModelError):
            ModelRegistry([build_openai("openai-main"), build_openai("openai-main")])


if __name__ == "__main__":
    unittest.main()
