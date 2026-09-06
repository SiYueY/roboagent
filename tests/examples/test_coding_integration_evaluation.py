from __future__ import annotations

import asyncio
from pathlib import Path

from examples.coding.evaluation import (
    EvaluationEvidence,
    EvaluationScenario,
    evaluate_scenario,
    verify_claim,
)
from examples.coding.harness import CodingConfig, create_coding_session
from roboagent import Agent
from roboagent.context import (
    CompactingContextManager,
    CompactionPolicy,
    ContextBudget,
    SummaryResult,
)
from roboagent.message import AssistantMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
)
from roboagent.runtime import Modality, RunStatus
from roboagent.tool import (
    ApplyPatchConfig,
    FilesystemConfig,
    FilesystemWorkspace,
    ShellConfig,
    ToolRegistry,
    create_apply_patch_tool,
    create_filesystem_tools,
    create_shell_tool,
)


class ScriptedProvider:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT})
    )

    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)

    async def stream(self, context, settings=None):
        text = next(self.replies)
        message = AssistantMessage(text)
        yield ResponseStarted("response", 0)
        yield TextDelta(1, text)
        yield ResponseCompleted(2, ModelResponse(message, FinishReason.STOP))


def _agent(root: Path, provider) -> Agent:
    filesystem = FilesystemConfig(FilesystemWorkspace(root))
    tools = [
        *create_filesystem_tools(filesystem),
        create_apply_patch_tool(ApplyPatchConfig(filesystem)),
    ]
    tools.append(
        create_shell_tool(ShellConfig(filesystem.workspace, default_timeout=10))
    )
    return Agent(provider, tool_registry=ToolRegistry(tuple(tools)))


def test_repository_understanding_uses_find_search_and_read(tmp_path: Path) -> None:
    async def check() -> None:
        (tmp_path / "module.py").write_text("def answer():\n    return 42\n")
        code = (
            "files = find_files(pattern='**/*.py')\n"
            "hits = search_files(query='answer', glob='**/*.py')\n"
            "source = read_file(path='module.py')\n"
            "final_answer({'files': files, 'hits': hits, 'has_return': 'return 42' in source})\n"
        )
        coding = create_coding_session(
            _agent(tmp_path, ScriptedProvider([f"```python\n{code}```"])),
            config=CodingConfig(observation_root=tmp_path / ".roboagent/artifacts"),
        )
        try:
            result = await coding.run("Understand the repository")
            outcome = evaluate_scenario(
                EvaluationScenario.REPOSITORY_UNDERSTANDING,
                EvaluationEvidence(result, coding.session.messages),
            )
            assert result.status is RunStatus.COMPLETED and outcome.passed, (
                result.error,
                outcome.reasons,
            )
            assert verify_claim(result, tool_name="read_file")
        finally:
            await coding.close()

    asyncio.run(check())


def test_bug_fix_runs_failing_test_patches_and_retests(tmp_path: Path) -> None:
    async def check() -> None:
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (tmp_path / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
        )
        patch = "*** Begin Patch\n*** Update File: calc.py\n@@\n def add(a, b):\n-    return a - b\n+    return a + b  # fixed\n*** End Patch\n"
        code = (
            "before = shell(command='python -m pytest -q test_calc.py')\n"
            "print('SHELL_EXIT=' + str(before['exit_code']))\n"
            "source = read_file(path='calc.py')\n"
            f"changed = apply_patch(patch={patch!r})\n"
            "after = shell(command='python -m pytest -q test_calc.py')\n"
            "print('SHELL_EXIT=' + str(after['exit_code']))\n"
            "final_answer({'fixed': after['exit_code'] == 0, 'inspected': 'a - b' in source})\n"
        )
        coding = create_coding_session(
            _agent(tmp_path, ScriptedProvider([f"```python\n{code}```"])),
            config=CodingConfig(observation_root=tmp_path / ".roboagent/artifacts"),
        )
        try:
            result = await coding.run("Fix the failing add test")
            outcome = evaluate_scenario(
                EvaluationScenario.BUG_FIX,
                EvaluationEvidence(result, coding.session.messages),
            )
            assert result.status is RunStatus.COMPLETED and outcome.passed, (
                outcome.reasons
            )
            assert "return a + b" in (tmp_path / "calc.py").read_text()
            assert verify_claim(result, tool_name="apply_patch")
            assert (
                sum(effect.tool_name == "apply_patch" for effect in result.effects) == 1
            )
        finally:
            await coding.close()

    asyncio.run(check())


def test_feature_change_and_failure_recovery(tmp_path: Path) -> None:
    async def check() -> None:
        (tmp_path / "README.md").write_text("demo\n")
        addition = (
            "*** Begin Patch\n*** Add File: feature.py\n+VALUE = 7\n*** End Patch\n"
        )
        conflict = "*** Begin Patch\n*** Update File: README.md\n@@\n-missing\n+changed\n*** End Patch\n"
        code = (
            "errors = []\n"
            "try:\n    read_file(path=1)\nexcept RoboAgentToolError as e:\n    errors.append(e.code)\n"
            "try:\n    read_file(path='missing.txt')\nexcept RoboAgentToolError as e:\n    errors.append(e.code)\n"
            f"try:\n    apply_patch(patch={conflict!r})\nexcept RoboAgentToolError as e:\n    errors.append(e.code)\n"
            "read_file(path='README.md')\n"
            f"apply_patch(patch={addition!r})\n"
            "test = shell(command=\"python -c 'import feature; assert feature.VALUE == 7'\")\n"
            "print('SHELL_EXIT=' + str(test['exit_code']))\n"
            "final_answer({'errors': errors, 'passed': test['exit_code'] == 0})\n"
        )
        coding = create_coding_session(
            _agent(tmp_path, ScriptedProvider([f"```python\n{code}```"])),
            config=CodingConfig(observation_root=tmp_path / ".roboagent/artifacts"),
        )
        try:
            result = await coding.run(
                "Add the feature and recover from expected failures"
            )
            feature = evaluate_scenario(
                EvaluationScenario.FEATURE_CHANGE,
                EvaluationEvidence(result, coding.session.messages),
            )
            recovery = evaluate_scenario(
                EvaluationScenario.FAILURE_RECOVERY,
                EvaluationEvidence(result, coding.session.messages),
            )
            assert feature.passed and recovery.passed
            assert (tmp_path / "feature.py").read_text() == "VALUE = 7\n"
            claim = evaluate_scenario(
                EvaluationScenario.CLAIM_VERIFICATION,
                EvaluationEvidence(result, coding.session.messages),
            )
            assert claim.passed
        finally:
            await coding.close()

    asyncio.run(check())


def test_steering_and_cancellation_evaluation(tmp_path: Path) -> None:
    async def check() -> None:
        release = asyncio.Event()

        class SteeringProvider(ScriptedProvider):
            def __init__(self):
                super().__init__(
                    [
                        "```python\nprint('first')\n```",
                        "```python\nfinal_answer('respected')\n```",
                    ]
                )
                self.calls = 0

            async def stream(self, context, settings=None):
                self.calls += 1
                if self.calls == 1:
                    await release.wait()
                async for event in super().stream(context, settings):
                    yield event

        coding = create_coding_session(
            _agent(tmp_path, SteeringProvider()),
            config=CodingConfig(observation_root=tmp_path / ".roboagent/artifacts"),
        )
        run = coding.start("Start")
        await coding.steer("Do not modify protected.py")
        release.set()
        result = await coding.wait(run)
        steering = evaluate_scenario(
            EvaluationScenario.STEERING,
            EvaluationEvidence(
                result,
                coding.session.messages,
                protected_path="protected.py",
                steer_record_sequence=0,
            ),
        )
        assert steering.passed
        await coding.close()

        cancelled = create_coding_session(
            Agent(ScriptedProvider(["```python\nwhile True:\n    pass\n```"])),
            config=CodingConfig(
                execution_timeout=10, observation_root=tmp_path / "cancel-artifacts"
            ),
        )
        cancel_task = asyncio.create_task(cancelled.run("cancel this"))
        for _ in range(100):
            if cancelled.worker.alive:
                break
            await asyncio.sleep(0.01)
        cancelled.cancel()
        cancelled_result = await asyncio.wait_for(cancel_task, 5)
        cancellation = evaluate_scenario(
            EvaluationScenario.CANCELLATION,
            EvaluationEvidence(
                cancelled_result,
                worker_alive=cancelled.worker.alive,
                active_run=cancelled.active_run is not None,
            ),
        )
        assert cancellation.passed
        await cancelled.close()

    asyncio.run(check())


def test_long_context_compacts_and_continues(tmp_path: Path) -> None:
    async def check() -> None:
        replies = []
        for index in range(4):
            replies.extend(("```python\nprint('x' * 300)\n```", f"turn {index} done"))
        provider = ScriptedProvider(replies)

        class BoundedSummarizer:
            async def summarize(self, *, existing_summary, messages, cancellation):
                cancellation.raise_if_cancelled()
                return SummaryResult(
                    "Prior coding turns produced bounded observations."
                )

        base = Agent(
            provider,
            context_manager=CompactingContextManager(
                budget=ContextBudget(max_tokens=520),
                summarizer=BoundedSummarizer(),
                policy=CompactionPolicy(target_ratio=1),
                provider_default_reserve=0,
            ),
        )
        coding = create_coding_session(
            base,
            config=CodingConfig(observation_root=tmp_path / ".roboagent/artifacts"),
        )
        count = 0
        result = None
        try:
            for index in range(4):
                run = coding.start(f"Long context turn {index}")
                subscription = run.subscribe()
                event_task = asyncio.create_task(_collect_events(subscription))
                result = await coding.wait(run)
                events = await event_task
                count += sum(
                    event.type == "context.compaction_completed" for event in events
                )
                subscription.close()
            assert result is not None
            outcome = evaluate_scenario(
                EvaluationScenario.LONG_CONTEXT,
                EvaluationEvidence(
                    result, coding.session.messages, compaction_count=count
                ),
            )
            assert result.status is RunStatus.COMPLETED and outcome.passed, (
                result.error,
                outcome.reasons,
            )
        finally:
            await coding.close()

    asyncio.run(check())


async def _collect_events(subscription):
    return [event async for event in subscription]
