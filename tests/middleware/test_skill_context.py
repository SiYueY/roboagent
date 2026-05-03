from __future__ import annotations

import unittest
from pathlib import Path

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

from roboagent.middleware import SkillContextMiddleware
from roboagent.skill import Skill


class SkillContextMiddlewareTests(unittest.TestCase):
    def test_build_skill_context_uses_active_skills_only(self) -> None:
        middleware = SkillContextMiddleware(
            [
                _skill("nav-plan", body="Use navigation."),
                _skill("old-nav", status="deprecated"),
                _skill("off-nav", enabled=False),
            ]
        )

        context = middleware.build_skill_context()

        self.assertIn("nav-plan@0.1.0", context)
        self.assertIn("Use navigation.", context)
        self.assertNotIn("old-nav", context)
        self.assertNotIn("off-nav", context)

    def test_empty_skills_do_not_change_prompt(self) -> None:
        middleware = SkillContextMiddleware()

        self.assertEqual(middleware.apply_to_system_prompt("Base."), "Base.")
        self.assertIsNone(middleware.apply_to_system_prompt(None))

    def test_wrap_model_call_augments_system_message(self) -> None:
        middleware = SkillContextMiddleware([_skill("nav-plan")])
        request = ModelRequest(
            model=object(),
            messages=[],
            system_message=SystemMessage(content="Base."),
        )
        captured: dict[str, str] = {}

        def handler(next_request: ModelRequest) -> ModelResponse:
            captured["system"] = next_request.system_message.content
            return ModelResponse(result=[AIMessage(content="ok")])

        middleware.wrap_model_call(request, handler)

        self.assertIn("Base.", captured["system"])
        self.assertIn("nav-plan@0.1.0", captured["system"])


def _skill(name: str, *, body: str = "", status: str = "active", enabled: bool = True) -> Skill:
    return Skill(
        name=name,
        description=f"Skill {name}.",
        source="test",
        source_dir=Path(f"/tmp/{name}"),
        body=body,
        status=status,
        enabled=enabled,
    )


if __name__ == "__main__":
    unittest.main()
