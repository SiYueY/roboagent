"""In-memory storage and matching for loaded skills."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from collections import Counter
from pathlib import Path
from typing import Final

from roboagent.skill.errors import DuplicateSkillError, SkillNotFoundError
from roboagent.skill.loader import SkillLoader
from roboagent.skill.skill import Skill

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")
_EXACT_NAME_MATCH_SCORE: Final = 100.0
_PARTIAL_NAME_MATCH_SCORE: Final = 40.0
_KEYWORD_MATCH_SCORE: Final = 8.0
_TAG_MATCH_SCORE: Final = 4.0
_DESCRIPTION_MATCH_SCORE: Final = 1.5
_METADATA_MATCH_SCORE: Final = 0.5


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


class SkillRegistry:
    """In-memory registry for loaded runtime skills.

    The registry is responsible for indexing skills by name, applying
    replacement policy, and providing lightweight matching over loaded skills.
    """

    def __init__(
        self,
        skills: Sequence[Skill] = (),
        *,
        loader: SkillLoader | None = None,
    ) -> None:
        self._skills: dict[str, Skill] = {}
        self._loader = loader or SkillLoader()
        if skills:
            self.register_many(skills)

    def register(self, skill: Skill, *, replace: bool = True) -> Skill:
        """Register a skill under its unique name.

        Args:
            skill: Runtime skill to register.
            replace: Whether an existing skill with the same name may be
                replaced.

        Returns:
            The registered skill.

        Raises:
            DuplicateSkillError: If `replace` is false and the skill name is
                already registered.
        """
        if not replace and skill.name in self._skills:
            raise DuplicateSkillError(f"Skill '{skill.name}' is already registered.")

        self._skills[skill.name] = skill
        logger.debug("Registered skill '%s' from source '%s'", skill.name, skill.source)
        return skill

    def register_many(self, skills: Iterable[Skill], *, replace: bool = True) -> list[Skill]:
        """Register multiple skills in order.

        Args:
            skills: Skills to register.
            replace: Whether existing registrations may be replaced.

        Returns:
            The registered skills in input order.
        """
        skill_list = list(skills)
        if not replace:
            names = [skill.name for skill in skill_list]
            duplicate_names = {name for name, count in Counter(names).items() if count > 1}
            if duplicate_names:
                duplicates = ", ".join(sorted(duplicate_names))
                raise DuplicateSkillError(f"Duplicate skill names in batch registration: {duplicates}")
            existing_names = sorted(name for name in names if name in self._skills)
            if existing_names:
                duplicates = ", ".join(existing_names)
                raise DuplicateSkillError(f"Skill names are already registered: {duplicates}")

        registered: list[Skill] = []
        for skill in skill_list:
            registered.append(self.register(skill, replace=replace))
        return registered

    @property
    def loader(self) -> SkillLoader:
        """Expose the loader used for source-based registration."""
        return self._loader

    def unregister(self, name: str, *, missing_ok: bool = True) -> bool:
        """Remove a skill from the registry.

        Args:
            name: Registered skill name.
            missing_ok: Whether missing skills should be ignored.

        Returns:
            `True` when a skill was removed, otherwise `False`.

        Raises:
            SkillNotFoundError: If the skill is absent and `missing_ok` is
                false.
        """
        if name not in self._skills:
            if missing_ok:
                return False
            raise SkillNotFoundError(f"Skill '{name}' is not registered.")

        del self._skills[name]
        logger.debug("Unregistered skill '%s'", name)
        return True

    def clear(self) -> None:
        """Remove all registered skills."""
        self._skills.clear()

    def get(self, name: str) -> Skill | None:
        """Return a skill by name if present."""
        return self._skills.get(name)

    def require(self, name: str) -> Skill:
        """Return a skill by name or raise.

        Args:
            name: Registered skill name.

        Returns:
            The matching runtime skill.

        Raises:
            SkillNotFoundError: If the skill is not registered.
        """
        skill = self.get(name)
        if skill is None:
            raise SkillNotFoundError(f"Skill '{name}' is not registered.")
        return skill

    def has(self, name: str) -> bool:
        """Return whether a skill is registered."""
        return name in self._skills

    def count(self) -> int:
        """Return the total number of registered skills."""
        return len(self._skills)

    def list_all(self, *, enabled_only: bool = False, source: str | None = None) -> list[Skill]:
        """Return registered skills with optional filtering.

        Args:
            enabled_only: Whether to exclude disabled skills.
            source: Optional source name filter.

        Returns:
            A name-sorted list of matching skills.
        """
        skills = list(self._skills.values())
        if enabled_only:
            skills = [skill for skill in skills if skill.enabled]
        if source is not None:
            skills = [skill for skill in skills if skill.source == source]
        skills.sort(key=lambda skill: skill.name)
        return skills

    def list_enabled(self) -> list[Skill]:
        """Return enabled skills sorted by name."""
        return self.list_all(enabled_only=True)

    def load_source(self, source: str | Path, *, replace: bool = True) -> list[Skill]:
        """Load skills from a source via the configured loader and register them.

        Args:
            source: Directory to load from.
            replace: Whether existing registrations may be replaced.

        Returns:
            The loaded skills from the source.
        """
        skills = self._loader.load_source(source)
        self.register_many(skills, replace=replace)
        return skills

    def load_all(
        self,
        sources: Sequence[str | Path] | None = None,
        *,
        replace: bool = True,
        clear: bool = False,
    ) -> list[Skill]:
        """Load all skills from the provided or configured sources.

        Args:
            sources: Optional source directories. Uses the loader's configured
                sources when omitted.
            replace: Whether existing registrations may be replaced.
            clear: Whether to clear the registry before loading.

        Returns:
            The loaded skills across all sources.
        """
        if clear:
            self.clear()
        skills = self._loader.load_all(sources)
        self.register_many(skills, replace=replace)
        return skills

    def match(
        self,
        query: str,
        *,
        top_k: int = 3,
        enabled_only: bool = True,
    ) -> list[Skill]:
        """Match the most relevant skills for a request.

        Args:
            query: Natural language request text.
            top_k: Maximum number of matches to return.
            enabled_only: Whether disabled skills should be excluded.

        Returns:
            A ranked list of matching skills.
        """
        if top_k <= 0:
            return []

        candidates = self.list_all(enabled_only=enabled_only)
        query_text = query.strip().lower()
        if not query_text:
            return candidates[:top_k]

        query_tokens = _tokenize(query_text)
        scored: list[tuple[float, Skill]] = []

        for skill in candidates:
            score = self._score_skill(skill, query_text, query_tokens)
            if score <= 0:
                continue
            scored.append((score, skill))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [skill for _, skill in scored[:top_k]]

    def _score_skill(self, skill: Skill, query_text: str, query_tokens: set[str]) -> float:
        """Return a simple lexical relevance score for a skill."""
        score = 0.0
        skill_name = skill.name.lower()

        if query_text == skill_name:
            score += _EXACT_NAME_MATCH_SCORE
        elif skill_name in query_text:
            score += _PARTIAL_NAME_MATCH_SCORE

        keyword_hits = 0
        for keyword in skill.trigger_keywords:
            keyword_tokens = _tokenize(keyword)
            if keyword_tokens and keyword_tokens & query_tokens:
                keyword_hits += 1
        score += keyword_hits * _KEYWORD_MATCH_SCORE

        tag_hits = sum(1 for tag in skill.tags if tag.lower() in query_tokens)
        score += tag_hits * _TAG_MATCH_SCORE

        description_hits = len(_tokenize(skill.description) & query_tokens)
        score += description_hits * _DESCRIPTION_MATCH_SCORE

        metadata_terms = " ".join(skill.metadata.values())
        metadata_hits = len(_tokenize(metadata_terms) & query_tokens)
        score += metadata_hits * _METADATA_MATCH_SCORE

        return score


__all__ = ["SkillRegistry"]
