from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from roboagent.agent import RuntimeContext, RuntimeFeatures, create_roboagent_runtime
from roboagent.config import AppConfig
from roboagent.middleware import RunJournalMiddleware, SkillContextMiddleware
from roboagent.runtime import MemoryRunEventStore, RunManager, RunStatus


class RuntimeFactoryTests(unittest.TestCase):
    def test_runtime_factory_uses_default_model(self) -> None:
        config = _app_config(default_model="openai-main")
        model = object()

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=model) as mock_create_model,
            patch("roboagent.agent.factory.AgentBuilder") as mock_builder,
        ):
            mock_builder.return_value.build.return_value = "graph"
            result = create_roboagent_runtime(config)

        self.assertEqual(result, "graph")
        args, kwargs = mock_create_model.call_args
        self.assertIsNone(args[0])
        self.assertIn("registry", kwargs)
        mock_builder.assert_called_once()
        self.assertIs(mock_builder.call_args.kwargs["model"], model)

    def test_runtime_context_model_name_overrides_default_model(self) -> None:
        config = _app_config(default_model="openai-main")

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=object()) as mock_create_model,
            patch("roboagent.agent.factory.AgentBuilder") as mock_builder,
        ):
            mock_builder.return_value.build.return_value = "graph"
            create_roboagent_runtime(
                config,
                runtime_context=RuntimeContext(model_name="deepseek-main", model_overrides={"temperature": 0.1}),
            )

        args, kwargs = mock_create_model.call_args
        self.assertEqual(args[0], "deepseek-main")
        self.assertEqual(kwargs["temperature"], 0.1)

    def test_runtime_factory_loads_configured_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "skills"
            skill_dir = source / "nav-plan"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "\n".join(
                    [
                        "---",
                        "name: nav-plan",
                        "description: Generate navigation plans.",
                        "---",
                        "",
                        "Use the navigation skill.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            config = _app_config(skills={"sources": [str(source)]})

            with (
                patch("roboagent.agent.factory.create_chat_model", return_value=object()),
                patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create_agent,
            ):
                result = create_roboagent_runtime(config)

        self.assertEqual(result, "graph")
        _, kwargs = mock_create_agent.call_args
        skill_context = next(
            item for item in kwargs["middleware"] if isinstance(item, SkillContextMiddleware)
        )
        self.assertIn("nav-plan@0.1.0", skill_context.build_skill_context())

    def test_skill_context_feature_can_be_disabled(self) -> None:
        config = _app_config()

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=object()),
            patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create_agent,
        ):
            create_roboagent_runtime(config, features=RuntimeFeatures(skill_context=False))

        _, kwargs = mock_create_agent.call_args
        self.assertFalse(any(isinstance(item, SkillContextMiddleware) for item in kwargs["middleware"]))

    def test_tool_resolution_feature_can_be_disabled(self) -> None:
        config = _app_config()

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=object()),
            patch("roboagent.agent.factory.AgentBuilder") as mock_builder,
        ):
            mock_builder.return_value.build.return_value = "graph"
            create_roboagent_runtime(config, features=RuntimeFeatures(tool_resolution=False))

        self.assertIsNone(mock_builder.call_args.kwargs["tool_manager"])

    def test_runtime_features_defaults_enable_base_capabilities(self) -> None:
        features = RuntimeFeatures()

        self.assertTrue(features.tool_resolution)
        self.assertTrue(features.skill_context)
        self.assertFalse(features.guardrails)
        self.assertFalse(features.run_journal)
        self.assertFalse(features.subagent)
        self.assertFalse(features.sandbox)

    def test_extra_middlewares_are_appended_to_default_chain(self) -> None:
        config = _app_config()
        extra = object()

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=object()),
            patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create_agent,
        ):
            create_roboagent_runtime(
                config,
                runtime_context=RuntimeContext(extra_middlewares=[extra]),
            )

        _, kwargs = mock_create_agent.call_args
        self.assertIs(kwargs["middleware"][-1], extra)

    def test_run_journal_feature_creates_run_and_middleware(self) -> None:
        config = _app_config()
        event_store = MemoryRunEventStore()
        run_manager = RunManager()

        with (
            patch("roboagent.agent.factory.create_chat_model", return_value=object()),
            patch("roboagent.agent.builder.create_agent", return_value="graph") as mock_create_agent,
        ):
            create_roboagent_runtime(
                config,
                runtime_context=RuntimeContext(
                    thread_id="thread-1",
                    run_id="run-1",
                    event_store=event_store,
                    run_manager=run_manager,
                ),
                features=RuntimeFeatures(run_journal=True),
            )

        _, kwargs = mock_create_agent.call_args
        self.assertTrue(any(isinstance(item, RunJournalMiddleware) for item in kwargs["middleware"]))
        self.assertEqual(run_manager.get("run-1").status, RunStatus.RUNNING)


def _app_config(
    *,
    default_model: str = "openai-main",
    skills: dict | None = None,
) -> AppConfig:
    return AppConfig.from_dict(
        {
            "default_model": default_model,
            "models": [
                {
                    "name": "openai-main",
                    "provider": "openai",
                    "params": {"model": "gpt-4o-mini"},
                },
                {
                    "name": "deepseek-main",
                    "provider": "deepseek",
                    "params": {"model": "deepseek-chat"},
                },
            ],
            "skills": skills or {},
        }
    )


if __name__ == "__main__":
    unittest.main()
