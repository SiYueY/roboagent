"""Skill catalog revision lifecycle and explicit read_skill Tool."""

from __future__ import annotations

from pathlib import Path

from roboagent.message import FrozenJsonObject
from roboagent.tool import (
    Tool,
    ToolContext,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolExecutionMode,
    ToolTextContent,
)

from .loader import SkillLoader
from .skill import SkillCatalog, SkillConfig, SkillDiagnostic, SkillReadError


class SkillManager:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
        config: SkillConfig | None = None,
        loader: SkillLoader | None = None,
    ) -> None:
        project = Path.cwd() if project_root is None else Path(project_root)
        user = Path.home() if user_root is None else Path(user_root)
        self.project_skills_root = project.resolve() / ".roboagent" / "skills"
        self.user_skills_root = user.resolve() / ".roboagent" / "skills"
        self.loader = loader or SkillLoader(config)
        self._catalog = SkillCatalog()
        self._diagnostics: tuple[SkillDiagnostic, ...] = ()
        self._runs: dict[str, SkillCatalog] = {}
        self.reload()

    @property
    def catalog(self) -> SkillCatalog:
        return self._catalog

    @property
    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return self._diagnostics

    def reload(self) -> SkillCatalog:
        self._catalog, self._diagnostics = self.loader.discover(self.project_skills_root, self.user_skills_root)
        return self._catalog

    def bind_run(self, run_id: str) -> SkillCatalog:
        catalog = self._catalog
        self._runs[run_id] = catalog
        return catalog

    def release_run(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def load(self, name: str, *, run_id: str | None = None) -> str:
        catalog = self._runs.get(run_id, self._catalog) if run_id is not None else self._catalog
        return catalog.load(name)


def create_read_skill_tool(manager: SkillManager) -> Tool:
    async def read(arguments: FrozenJsonObject, context: ToolContext) -> ToolTextContent:
        name = arguments["name"]
        assert isinstance(name, str)
        try:
            return ToolTextContent(manager.load(name, run_id=context.run_id))
        except SkillReadError as exc:
            raise ToolExecutionFailure(ToolErrorInfo(exc.code, str(exc))) from exc
        except OSError as exc:
            raise ToolExecutionFailure(ToolErrorInfo("skill_read_error", "Could not read skill.")) from exc

    return Tool(
        ToolDefinition(
            "read_skill",
            "Read the instructions for one available RoboAgent skill.",
            FrozenJsonObject(
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                }
            ),
        ),
        read,
        ToolExecutionMode.CONCURRENT,
        ToolEffectKind.READ_ONLY,
    )
