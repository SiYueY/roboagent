"""Deterministic, non-recursive SKILL.md discovery."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml
from yaml.tokens import AliasToken, AnchorToken, TagToken

from .skill import (
    SkillCatalog,
    SkillConfig,
    SkillDiagnostic,
    SkillEntry,
    SkillMetadata,
    SkillSource,
)

SKILL_FILE_NAME = "SKILL.md"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class SkillLoader:
    def __init__(self, config: SkillConfig | None = None) -> None:
        self.config = config or SkillConfig()

    def discover(
        self,
        project_root: Path,
        user_root: Path,
    ) -> tuple[SkillCatalog, tuple[SkillDiagnostic, ...]]:
        project, project_diagnostics = self._source(project_root, SkillSource.PROJECT)
        user, user_diagnostics = self._source(user_root, SkillSource.USER)
        diagnostics = [*project_diagnostics, *user_diagnostics]
        selected: dict[str, SkillEntry] = {entry.metadata.name: entry for entry in user}
        for entry in project:
            old = selected.get(entry.metadata.name)
            if old is not None:
                diagnostics.append(
                    SkillDiagnostic(
                        "skill_overridden",
                        entry.metadata.name,
                        selected_path=entry.metadata.path,
                        ignored_path=old.metadata.path,
                    )
                )
            selected[entry.metadata.name] = entry
        entries = tuple(sorted(selected.values(), key=lambda entry: (entry.metadata.name, entry.metadata.source.value)))
        return SkillCatalog(entries), tuple(diagnostics)

    def _source(self, root: Path, source: SkillSource) -> tuple[tuple[SkillEntry, ...], tuple[SkillDiagnostic, ...]]:
        if not root.exists() or not root.is_dir():
            return (), ()
        parsed: list[SkillEntry] = []
        diagnostics: list[SkillDiagnostic] = []
        directories = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.name,
        )
        for directory in directories:
            path = directory / SKILL_FILE_NAME
            if path.is_symlink():
                diagnostics.append(
                    SkillDiagnostic(
                        "invalid_skill",
                        source=source,
                        paths=(path.resolve(),),
                        message="SKILL.md symlinks are not allowed.",
                    )
                )
                continue
            if not path.is_file():
                continue
            try:
                parsed.append(self._parse(path.resolve(), source))
            except Exception as exc:
                diagnostics.append(SkillDiagnostic("invalid_skill", source=source, paths=(path.resolve(),), message=str(exc)))
        counts = Counter(entry.metadata.name for entry in parsed)
        duplicates = {name for name, count in counts.items() if count > 1}
        for name in sorted(duplicates):
            paths = tuple(sorted((entry.metadata.path for entry in parsed if entry.metadata.name == name), key=str))
            diagnostics.append(SkillDiagnostic("duplicate_skill_name", name, source, paths))
        return tuple(entry for entry in parsed if entry.metadata.name not in duplicates), tuple(diagnostics)

    def _parse(self, path: Path, source: SkillSource) -> SkillEntry:
        raw = path.read_bytes()
        if len(raw) > self.config.max_body_bytes:
            raise ValueError("skill_too_large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("skill_read_error") from exc
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        frontmatter, body = _split_frontmatter(text)
        tokens = tuple(yaml.scan(frontmatter))
        if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in tokens):
            raise ValueError("YAML tags, aliases, and anchors are not allowed.")
        data = yaml.load(frontmatter, Loader=_UniqueKeySafeLoader)
        if not isinstance(data, dict):
            raise ValueError("Skill frontmatter must be a mapping.")
        name = data.get("name")
        description = data.get("description")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Invalid skill name.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Skill description is required.")
        normalized = _normalize_description(description, self.config.max_description_chars)
        return SkillEntry(SkillMetadata(name, normalized, path, source), body)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("Skill file must start with YAML frontmatter.")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("Skill frontmatter is not closed.")
    suffix = text[end + 4 :]
    if suffix and not suffix.startswith("\n"):
        raise ValueError("Closing frontmatter delimiter must occupy its own line.")
    return text[4:end], suffix[1:] if suffix.startswith("\n") else ""


def _normalize_description(value: str, limit: int) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return "…" if limit == 1 else value[: limit - 1] + "…"
