"""Execution boundary for RoboAgent skills."""

from __future__ import annotations

import importlib
import inspect
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError

from roboagent.skill.errors import (
    SkillEntrypointError,
    SkillExecutionError,
    SkillPermissionError,
    SkillValidationError,
)
from roboagent.skill.skill import Skill, SkillSchemaRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillExecutionResult:
    """Structured record produced by one skill execution attempt."""

    skill_name: str
    version: str
    success: bool
    latency_seconds: float
    output: BaseModel | None = None
    error: str | None = None
    error_type: str | None = None
    permissions_checked: tuple[str, ...] = ()
    source: str | None = None


class SkillExecutor:
    """Validate, invoke, and normalize executable skill results."""

    def __init__(
        self,
        *,
        allowed_permissions: Sequence[str] = (),
        require_permissions: bool = True,
    ) -> None:
        self.allowed_permissions = tuple(allowed_permissions)
        self.require_permissions = require_permissions

    async def execute(
        self,
        skill: Skill,
        payload: Mapping[str, Any] | BaseModel,
        *,
        context: Mapping[str, Any] | None = None,
        allowed_permissions: Sequence[str] | None = None,
    ) -> SkillExecutionResult:
        """Execute one skill and return a normalized execution record."""
        started = time.perf_counter()
        checked_permissions = skill.required_permissions

        try:
            self._ensure_executable(skill)
            self._check_permissions(skill, allowed_permissions=allowed_permissions)
            handler = self._resolve_entrypoint(skill.entrypoint)
            input_schema = self._resolve_input_schema(skill, handler)
            output_schema = self._resolve_output_schema(skill, handler)
            validated_input = self._validate_input(input_schema, payload)
            raw_output = await self._invoke(handler, validated_input, context=context)
            output = self._validate_output(output_schema, raw_output)
            latency = time.perf_counter() - started
            logger.info(
                "Executed skill '%s' version '%s' in %.4fs",
                skill.name,
                skill.version,
                latency,
            )
            return SkillExecutionResult(
                skill_name=skill.name,
                version=skill.version,
                success=True,
                latency_seconds=latency,
                output=output,
                permissions_checked=checked_permissions,
                source=skill.source,
            )
        except SkillExecutionError as exc:
            return self._failure(skill, started, exc, checked_permissions)
        except Exception as exc:  # pragma: no cover - defensive normalization
            wrapped = SkillExecutionError(f"Skill handler failed: {exc}")
            return self._failure(skill, started, wrapped, checked_permissions)

    def _ensure_executable(self, skill: Skill) -> None:
        if not skill.enabled:
            raise SkillExecutionError(f"Skill '{skill.name}' is disabled.")
        if skill.status != "active":
            raise SkillExecutionError(f"Skill '{skill.name}' is not active: {skill.status}.")
        if not skill.entrypoint:
            raise SkillEntrypointError(f"Skill '{skill.name}' does not declare an entrypoint.")

    def _check_permissions(
        self,
        skill: Skill,
        *,
        allowed_permissions: Sequence[str] | None,
    ) -> None:
        if not self.require_permissions or not skill.required_permissions:
            return

        allowed = set(self.allowed_permissions if allowed_permissions is None else allowed_permissions)
        missing = sorted(set(skill.required_permissions) - allowed)
        if missing:
            raise SkillPermissionError(
                f"Skill '{skill.name}' requires missing permissions: {', '.join(missing)}"
            )

    def _resolve_entrypoint(self, entrypoint: str | None) -> Callable[..., Any]:
        if not entrypoint:
            raise SkillEntrypointError("Missing skill entrypoint.")
        module_name, _, attr_name = entrypoint.partition(":")
        if not module_name or not attr_name:
            raise SkillEntrypointError(f"Invalid skill entrypoint: {entrypoint}")
        try:
            module = importlib.import_module(module_name)
            handler = getattr(module, attr_name)
        except (ImportError, AttributeError) as exc:
            raise SkillEntrypointError(f"Could not resolve skill entrypoint: {entrypoint}") from exc
        if not callable(handler):
            raise SkillEntrypointError(f"Skill entrypoint is not callable: {entrypoint}")
        return handler

    def _resolve_input_schema(self, skill: Skill, handler: Callable[..., Any]) -> type[BaseModel]:
        if skill.input_schema is not None:
            return self._resolve_schema_ref(skill.input_schema)

        signature = inspect.signature(handler)
        type_hints = get_type_hints(handler)
        for parameter in signature.parameters.values():
            if parameter.name == "context":
                continue
            annotation = type_hints.get(parameter.name, parameter.annotation)
            if _is_basemodel_type(annotation):
                return annotation
            break
        raise SkillEntrypointError(f"Skill '{skill.name}' must declare a Pydantic input schema.")

    def _resolve_output_schema(self, skill: Skill, handler: Callable[..., Any]) -> type[BaseModel]:
        if skill.output_schema is not None:
            return self._resolve_schema_ref(skill.output_schema)

        annotation = get_type_hints(handler).get("return", inspect.signature(handler).return_annotation)
        if _is_basemodel_type(annotation):
            return annotation
        raise SkillEntrypointError(f"Skill '{skill.name}' must declare a Pydantic output schema.")

    def _resolve_schema_ref(self, schema_ref: SkillSchemaRef) -> type[BaseModel]:
        if _is_basemodel_type(schema_ref):
            return schema_ref
        if not isinstance(schema_ref, str):
            raise SkillEntrypointError("Skill schema reference must be a Pydantic model or import path.")

        module_name, _, attr_name = schema_ref.partition(":")
        if not module_name or not attr_name:
            raise SkillEntrypointError(f"Invalid skill schema reference: {schema_ref}")
        try:
            module = importlib.import_module(module_name)
            schema = getattr(module, attr_name)
        except (ImportError, AttributeError) as exc:
            raise SkillEntrypointError(f"Could not resolve skill schema reference: {schema_ref}") from exc
        if not _is_basemodel_type(schema):
            raise SkillEntrypointError(f"Skill schema reference is not a Pydantic model: {schema_ref}")
        return schema

    def _validate_input(
        self,
        input_schema: type[BaseModel],
        payload: Mapping[str, Any] | BaseModel,
    ) -> BaseModel:
        try:
            if isinstance(payload, input_schema):
                return payload
            return input_schema.model_validate(payload)
        except ValidationError as exc:
            raise SkillValidationError(f"Skill input validation failed: {exc}") from exc

    def _validate_output(self, output_schema: type[BaseModel], output: Any) -> BaseModel:
        try:
            if isinstance(output, output_schema):
                return output
            return output_schema.model_validate(output)
        except ValidationError as exc:
            raise SkillValidationError(f"Skill output validation failed: {exc}") from exc

    async def _invoke(
        self,
        handler: Callable[..., Any],
        payload: BaseModel,
        *,
        context: Mapping[str, Any] | None,
    ) -> Any:
        signature = inspect.signature(handler)
        if "context" in signature.parameters:
            result = handler(payload, context=dict(context or {}))
        else:
            result = handler(payload)
        if inspect.isawaitable(result):
            return await result
        return result

    def _failure(
        self,
        skill: Skill,
        started: float,
        exc: Exception,
        checked_permissions: tuple[str, ...],
    ) -> SkillExecutionResult:
        latency = time.perf_counter() - started
        logger.warning(
            "Skill '%s' version '%s' failed in %.4fs: %s",
            skill.name,
            skill.version,
            latency,
            exc,
        )
        return SkillExecutionResult(
            skill_name=skill.name,
            version=skill.version,
            success=False,
            latency_seconds=latency,
            error=str(exc),
            error_type=type(exc).__name__,
            permissions_checked=checked_permissions,
            source=skill.source,
        )


def _is_basemodel_type(value: Any) -> bool:
    return inspect.isclass(value) and issubclass(value, BaseModel)


__all__ = ["SkillExecutionResult", "SkillExecutor"]
