from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from roboagent.skill import Skill, SkillExecutor


class SkillExecutorTests(unittest.TestCase):
    def test_execute_successfully_validates_input_and_output(self) -> None:
        with _skill_module() as module_name:
            skill = _skill(
                entrypoint=f"{module_name}:run",
                required_permissions=("tool:map.read",),
            )
            executor = SkillExecutor(allowed_permissions=("tool:map.read",))

            result = asyncio.run(executor.execute(skill, {"query": "dock"}))

        self.assertTrue(result.success)
        self.assertEqual(result.output.summary, "planned:dock")
        self.assertEqual(result.permissions_checked, ("tool:map.read",))

    def test_missing_entrypoint_returns_failure_result(self) -> None:
        skill = _skill(entrypoint="missing.module:run")
        executor = SkillExecutor(require_permissions=False)

        result = asyncio.run(executor.execute(skill, {"query": "dock"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "SkillEntrypointError")

    def test_input_validation_failure_returns_failure_result(self) -> None:
        with _skill_module() as module_name:
            skill = _skill(entrypoint=f"{module_name}:run")
            executor = SkillExecutor(require_permissions=False)

            result = asyncio.run(executor.execute(skill, {"query": ""}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "SkillValidationError")

    def test_output_validation_failure_returns_failure_result(self) -> None:
        with _skill_module(bad_output=True) as module_name:
            skill = _skill(entrypoint=f"{module_name}:run")
            executor = SkillExecutor(require_permissions=False)

            result = asyncio.run(executor.execute(skill, {"query": "dock"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "SkillValidationError")

    def test_permission_failure_returns_failure_result(self) -> None:
        with _skill_module() as module_name:
            skill = _skill(
                entrypoint=f"{module_name}:run",
                required_permissions=("tool:map.read",),
            )
            executor = SkillExecutor(allowed_permissions=())

            result = asyncio.run(executor.execute(skill, {"query": "dock"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "SkillPermissionError")


class _skill_module:
    def __init__(self, *, bad_output: bool = False) -> None:
        self.bad_output = bad_output
        self.tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self.module_name = "executor_skill_fixture"

    def __enter__(self) -> str:
        self.tmpdir = tempfile.TemporaryDirectory()
        module_path = Path(self.tmpdir.name) / f"{self.module_name}.py"
        output_line = "return {'summary': None}" if self.bad_output else "return SkillOutput(summary=f'planned:{payload.query}')"
        module_path.write_text(
            "\n".join(
                [
                    "from pydantic import BaseModel, Field",
                    "",
                    "class SkillInput(BaseModel):",
                    "    query: str = Field(min_length=1)",
                    "",
                    "class SkillOutput(BaseModel):",
                    "    summary: str",
                    "",
                    "async def run(payload: SkillInput) -> SkillOutput:",
                    f"    {output_line}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        sys.path.insert(0, self.tmpdir.name)
        sys.modules.pop(self.module_name, None)
        return self.module_name

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.tmpdir is None:
            return
        sys.modules.pop(self.module_name, None)
        if self.tmpdir.name in sys.path:
            sys.path.remove(self.tmpdir.name)
        self.tmpdir.cleanup()


def _skill(
    *,
    entrypoint: str,
    required_permissions: tuple[str, ...] = (),
) -> Skill:
    return Skill(
        name="nav-plan",
        description="Generate navigation plans.",
        source="test",
        source_dir=Path("/tmp/nav-plan"),
        entrypoint=entrypoint,
        required_permissions=required_permissions,
    )


if __name__ == "__main__":
    unittest.main()
