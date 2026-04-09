"""Public facade for the RoboAgent skill subsystem."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from roboagent.skill.loader import SkillLoader
from roboagent.skill.registry import SkillRegistry
from roboagent.skill.skill import Skill

logger = logging.getLogger(__name__)


class SkillManager:
    """Unified public interface for the skill subsystem.

    `SkillManager` is intentionally kept as the main entry point for external
    callers. It coordinates loader and registry behavior while exposing a
    compact management-oriented API.
    """

    def __init__(
        self,
        *,
        sources: Sequence[str | Path] = (),
        loader: SkillLoader | None = None,
        registry: SkillRegistry | None = None,
    ) -> None:
        self._loader = loader or SkillLoader(sources=sources)
        self._registry = registry or SkillRegistry(loader=self._loader)
        self._sources = tuple(Path(source) for source in sources) or tuple(self._registry.loader.sources)

    @property
    def registry(self) -> SkillRegistry:
        """Expose the underlying registry for advanced integrations."""
        return self._registry

    def load(
        self,
        sources: Sequence[str | Path] | None = None,
        *,
        replace: bool = True,
        clear: bool = False,
    ) -> list[Skill]:
        """Load skills from configured or provided sources into the registry.

        Args:
            sources: Optional source directories. Uses the manager's configured
                sources when omitted.
            replace: Whether existing registrations may be replaced.
            clear: Whether to clear the registry before loading.

        Returns:
            The loaded runtime skills.
        """
        resolved_sources = tuple(Path(source) for source in (sources or self._sources))
        loaded = self._registry.load_all(resolved_sources, replace=replace, clear=clear)
        logger.debug("Loaded %d skills from %d sources", len(loaded), len(resolved_sources))
        return loaded

    def reload(self) -> list[Skill]:
        """Reload all configured sources from scratch.

        Returns:
            The reloaded runtime skills.
        """
        return self.load(clear=True)

    def register(self, skill: Skill, *, replace: bool = True) -> Skill:
        """Register a single runtime skill.

        Args:
            skill: Runtime skill to register.
            replace: Whether an existing registration may be replaced.

        Returns:
            The registered skill.
        """
        return self._registry.register(skill, replace=replace)

    def unregister(self, name: str, *, missing_ok: bool = True) -> bool:
        """Remove a skill from the registry.

        Args:
            name: Registered skill name.
            missing_ok: Whether missing skills should be ignored.

        Returns:
            `True` when a skill was removed, otherwise `False`.
        """
        return self._registry.unregister(name, missing_ok=missing_ok)

    def list_skills(self, *, enabled_only: bool = False, source: str | None = None) -> list[Skill]:
        """List known skills.

        Args:
            enabled_only: Whether to exclude disabled skills.
            source: Optional source filter.

        Returns:
            Matching runtime skills.
        """
        return self._registry.list_all(enabled_only=enabled_only, source=source)

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name.

        Args:
            name: Registered skill name.

        Returns:
            The matching runtime skill, or `None` when absent.
        """
        return self._registry.get(name)

    def has_skill(self, name: str) -> bool:
        """Return whether a skill exists in the registry.

        Args:
            name: Registered skill name.

        Returns:
            `True` if the skill exists, otherwise `False`.
        """
        return self._registry.has(name)

    def is_enabled(self, name: str) -> bool:
        """Return whether a registered skill exists and is enabled.

        Args:
            name: Registered skill name.

        Returns:
            `True` when the skill exists and is enabled.
        """
        skill = self._registry.get(name)
        return bool(skill and skill.enabled)

    def enable(self, name: str) -> Skill:
        """Enable a skill in the registry.

        Args:
            name: Registered skill name.

        Returns:
            The updated runtime skill.
        """
        return self._set_enabled(name, enabled=True)

    def disable(self, name: str) -> Skill:
        """Disable a skill in the registry.

        Args:
            name: Registered skill name.

        Returns:
            The updated runtime skill.
        """
        return self._set_enabled(name, enabled=False)

    def _set_enabled(self, name: str, *, enabled: bool) -> Skill:
        """Update the enabled flag of a registered skill."""
        skill = self._registry.require(name)
        updated = replace(skill, enabled=enabled)
        self._registry.register(updated, replace=True)
        return updated

    def select(self, query: str, *, top_k: int = 3, enabled_only: bool = True) -> list[Skill]:
        """Select the most relevant skills for a request.

        Args:
            query: Natural language request text.
            top_k: Maximum number of skills to return.
            enabled_only: Whether disabled skills should be excluded.

        Returns:
            Ranked matching skills.
        """
        return self._registry.match(query, top_k=top_k, enabled_only=enabled_only)


__all__ = ["SkillManager"]
