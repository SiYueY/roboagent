"""In-memory storage for configured provider-backed chat models."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from roboagent.model.errors import DuplicateModelError, ModelConfigError, ModelNotFoundError
from roboagent.model.providers import ProviderModelConfig


class ModelRegistry:
    """In-memory registry for provider model configurations.

    The registry keeps model entries keyed by `name`, enforces uniqueness,
    validates the configured default model, and resolves a concrete model entry
    for factory instantiation.
    """

    def __init__(
        self,
        models: Iterable[ProviderModelConfig] = (),
        *,
        default_model: str | None = None,
    ) -> None:
        self._models: dict[str, ProviderModelConfig] = {}
        self._default_model = default_model
        if models:
            self.register_batch(models)
        self._validate_default_model()

    @property
    def default_model(self) -> str | None:
        """Return the configured default model name, if any."""
        return self._default_model

    def set_default_model(self, name: str | None) -> None:
        """Set the default model name and validate it against registry entries.

        Args:
            name: Model name to use as default, or `None` to clear explicit
                default behavior.

        Raises:
            ModelConfigError: If `name` is non-null and absent from the registry.
        """
        self._default_model = name
        self._validate_default_model()

    def register(self, model: ProviderModelConfig, /) -> ProviderModelConfig:
        """Register one model configuration.

        Args:
            model: Model configuration to register.

        Returns:
            The registered model.

        Raises:
            DuplicateModelError: If the model name is already known.
            ModelConfigError: If default model validation fails after mutation.
        """
        result = self._register_one(model)
        self._validate_default_model()
        return result

    def register_batch(self, models: Iterable[ProviderModelConfig], /) -> list[ProviderModelConfig]:
        """Register multiple model configurations in order.

        Args:
            models: Model configurations to register.

        Returns:
            Registered models in input order.

        Raises:
            DuplicateModelError: If duplicate names are present.
            ModelConfigError: If default model validation fails after mutation.
        """
        result = self._register_batch(models)
        self._validate_default_model()
        return result

    def _register_one(self, model: ProviderModelConfig) -> ProviderModelConfig:
        """Register one model configuration under its unique name."""
        if model.name in self._models:
            raise DuplicateModelError(f"Model '{model.name}' is already registered.")
        self._models[model.name] = model
        return model

    def _register_batch(self, models: Iterable[ProviderModelConfig]) -> list[ProviderModelConfig]:
        """Register multiple model configurations in order.

        Args:
            models: Model configurations to register.

        Returns:
            Registered models in input order.

        Raises:
            DuplicateModelError: If duplicate names are present.
        """
        model_list = list(models)
        names = [model.name for model in model_list]
        duplicate_names = {name for name, count in Counter(names).items() if count > 1}
        if duplicate_names:
            duplicates = ", ".join(sorted(duplicate_names))
            raise DuplicateModelError(f"Duplicate model names in batch registration: {duplicates}")

        existing_names = sorted(name for name in names if name in self._models)
        if existing_names:
            duplicates = ", ".join(existing_names)
            raise DuplicateModelError(f"Model names are already registered: {duplicates}")

        registered: list[ProviderModelConfig] = []
        for model in model_list:
            registered.append(self._register_one(model))
        return registered

    def _validate_default_model(self) -> None:
        """Validate that the configured default model exists.

        Raises:
            ModelConfigError: If the default model is configured but absent.
        """
        if self._default_model is None:
            return
        if self._default_model not in self._models:
            raise ModelConfigError(
                f"Default model '{self._default_model}' is not registered."
            )

    def get(self, name: str) -> ProviderModelConfig | None:
        """Return a model configuration by name if present."""
        return self._models.get(name)

    def require(self, name: str) -> ProviderModelConfig:
        """Return a model configuration by name or raise.

        Args:
            name: Registered model name.

        Returns:
            The matching model configuration.

        Raises:
            ModelNotFoundError: If the model is not registered.
        """
        model = self.get(name)
        if model is None:
            raise ModelNotFoundError(f"Model '{name}' is not registered.")
        return model

    def has(self, name: str) -> bool:
        """Return whether a model is registered."""
        return name in self._models

    def list_all(self, *, enabled_only: bool = False) -> list[ProviderModelConfig]:
        """Return registered model configurations.

        Args:
            enabled_only: Whether to exclude disabled model entries.

        Returns:
            Name-sorted registered model entries.
        """
        models = list(self._models.values())
        if enabled_only:
            models = [model for model in models if model.enabled]
        models.sort(key=lambda item: item.name)
        return models

    def resolve(self, name: str | None = None, *, enabled_only: bool = True) -> ProviderModelConfig:
        """Resolve one model entry by explicit name or default selection rules.

        Args:
            name: Explicit model name. When omitted, resolves in this order:
                configured default model, then first enabled registered model.
            enabled_only: Whether disabled models are disallowed.

        Returns:
            The resolved model configuration.

        Raises:
            ModelNotFoundError: If no matching model can be resolved.
        """
        if name is not None:
            model = self.require(name)
            if enabled_only and not model.enabled:
                raise ModelNotFoundError(f"Model '{name}' is disabled.")
            return model

        if self._default_model is not None:
            model = self.require(self._default_model)
            if enabled_only and not model.enabled:
                raise ModelNotFoundError(f"Default model '{self._default_model}' is disabled.")
            return model

        for model in self._models.values():
            if not enabled_only or model.enabled:
                return model

        raise ModelNotFoundError("No model is available for resolution.")

    def count(self) -> int:
        """Return the total number of registered models."""
        return len(self._models)

    def clear(self) -> None:
        """Remove all registered model entries."""
        self._models.clear()


__all__ = ["ModelRegistry"]
