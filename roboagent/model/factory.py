"""Configured model resolution and provider lifecycle."""

from __future__ import annotations

import inspect
from typing import Any

from roboagent.model.client import Model
from roboagent.model.errors import ModelProviderError
from roboagent.model.providers import (
    DeepSeekModelConfig,
    OpenAIModelConfig,
    TongyiModelConfig,
    create_deepseek_model,
    create_openai_model,
    create_tongyi_model,
)
from roboagent.model.registry import ModelRegistry


def create_model(name: str | None = None, *, registry: ModelRegistry, **kwargs: Any) -> Model:
    try:
        config = registry.resolve(name)
    except Exception as exc:
        raise ModelProviderError("model_resolution_failure", f"Cannot resolve model {name!r}.") from exc
    if isinstance(config, OpenAIModelConfig):
        return create_openai_model(config, **kwargs)
    if isinstance(config, DeepSeekModelConfig):
        return create_deepseek_model(config, **kwargs)
    if isinstance(config, TongyiModelConfig):
        return create_tongyi_model(config, **kwargs)
    raise ModelProviderError("model_resolution_failure", f"Unsupported provider {config.provider!r}.")


class ConfiguredModelProvider:
    def __init__(self, registry: ModelRegistry, **model_overrides: Any) -> None:
        self.registry = registry
        self.model_overrides = model_overrides
        self._models: dict[str, Model] = {}
        self._clients: dict[tuple[object, ...], object] = {}
        self._closed = False

    def get_model(self, name: str) -> Model:
        if self._closed:
            raise ModelProviderError("provider_closed", "ModelProvider is closed.")
        try:
            if name not in self._models:
                model = create_model(name, registry=self.registry, **self.model_overrides)
                self._attach_shared_client(model)
                self._models[name] = model
            return self._models[name]
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError("model_resolution_failure", f"Cannot resolve model {name!r}.") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failures: list[BaseException] = []
        for client in self._clients.values():
            close = getattr(client, "close", None)
            try:
                value = close() if close is not None else None
                if inspect.isawaitable(value):
                    await value
            except BaseException as exc:
                failures.append(exc)
        self._models.clear()
        self._clients.clear()
        if failures:
            raise ModelProviderError("provider_close_failure", "One or more Models failed to close.") from failures[0]

    def _attach_shared_client(self, model: Model) -> None:
        from roboagent.model.client import OpenAICompatibleModel

        if not isinstance(model, OpenAICompatibleModel) or model.client is not None:
            return
        key = (
            model.base_url,
            model.api_key,
            model.organization,
            model.max_retries,
            model.request_timeout,
            tuple(sorted((model.default_headers or {}).items())),
            _stable_client_value(model.default_query),
        )
        if key not in self._clients:
            from openai import AsyncOpenAI

            self._clients[key] = AsyncOpenAI(
                api_key=model.api_key,
                base_url=model.base_url,
                organization=model.organization,
                max_retries=2 if model.max_retries is None else model.max_retries,
                timeout=model.request_timeout,
                default_headers=model.default_headers,
                default_query=model.default_query,
            )
        model.client = self._clients[key]


def _stable_client_value(value: object) -> str | None:
    if value is None:
        return None
    from roboagent.message import canonical_json_dumps

    return canonical_json_dumps(value)
