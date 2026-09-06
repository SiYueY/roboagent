from __future__ import annotations

import asyncio
from pathlib import Path

from examples.coding.harness import CodingConfig, create_coding_session
from roboagent import Agent
from roboagent.message import AssistantMessage, FrozenJsonObject, TextContent
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    Usage,
    UsageUpdated,
)
from roboagent.runtime import Modality, RetryBlockerCode, RunStatus
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolRegistry,
    ToolTextContent,
)


class CodingProvider:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT})
    )

    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.contexts = []

    async def stream(self, context, settings=None):
        self.contexts.append(context)
        text = next(self.replies)
        message = AssistantMessage(text)
        yield ResponseStarted("response", 0)
        yield TextDelta(1, text)
        yield UsageUpdated(2, Usage(1, 1, 2))
        yield ResponseCompleted(
            3, ModelResponse(message, FinishReason.STOP, Usage(1, 1, 2))
        )


def test_coding_session_worker_bridge_and_local_final(tmp_path: Path) -> None:
    async def check() -> None:
        calls = []

        async def read(arguments, context):
            calls.append(dict(arguments))
            return ToolTextContent("hello")

        tool = Tool(
            ToolDefinition(
                "read_file",
                "Read a file.",
                FrozenJsonObject(
                    {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    }
                ),
            ),
            read,
        )
        provider = CodingProvider(
            [
                "```python\nx = read_file(path='README.md')\nprint(x)\n```",
                "```python\nfinal_answer(x)\n```",
            ]
        )
        coding = create_coding_session(
            Agent(provider, tool_registry=ToolRegistry((tool,))),
            config=CodingConfig(observation_root=tmp_path / "artifacts"),
        )
        try:
            result = await coding.run("inspect")
            assert result.status is RunStatus.COMPLETED
            assert result.output is not None
            assert result.output.content == (TextContent("hello"),)
            assert calls == [{"path": "README.md"}]
            assert len(provider.contexts) == 2
            assert all(context.tools == () for context in provider.contexts)
            assert "read_file(path)" in coding.session.agent.prompt.system
            assert "never emit that label" in coding.session.agent.prompt.system
            projected_action = provider.contexts[1].segments[-2].message.content[0].text
            assert "Python action:\n```python\n" in projected_action
            projected_observation = (
                provider.contexts[1].segments[-1].message.content[0].text
            )
            assert "Protocol reminder:" in projected_observation
            assert "never emit that label" in projected_observation
            assert any(
                record.tool_name == "read_file" for record in result.execution_records
            )
        finally:
            await coding.close()

    asyncio.run(check())


def test_large_observation_artifact_owned_and_cleaned(tmp_path: Path) -> None:
    async def check() -> None:
        provider = CodingProvider(
            [
                "```python\nprint('x' * 200)\n```",
                "done",
            ]
        )
        coding = create_coding_session(
            Agent(provider),
            config=CodingConfig(
                observation_root=tmp_path / "artifacts",
                inline_observation_bytes=16,
                max_observation_bytes=1024,
            ),
        )
        result = await coding.run("go")
        assert result.status is RunStatus.COMPLETED
        assert len(coding.observations.items) == 1
        observation = next(iter(coding.observations.items.values()))
        assert observation.status == "REFERENCED"
        assert observation.path.exists()
        await coding.close()
        assert not observation.path.exists()

    asyncio.run(check())


def test_two_coding_sessions_do_not_share_worker_or_state(tmp_path: Path) -> None:
    async def check() -> None:
        first = create_coding_session(
            Agent(CodingProvider(["done"])),
            config=CodingConfig(observation_root=tmp_path / "a"),
        )
        second = create_coding_session(
            Agent(CodingProvider(["done"])),
            config=CodingConfig(observation_root=tmp_path / "b"),
        )
        try:
            assert first.worker is not second.worker
            assert first.adapter is not second.adapter
            assert first.session.agent.tool_registry.get(
                "execute_python"
            ) is not second.session.agent.tool_registry.get("execute_python")
        finally:
            await first.close()
            await second.close()

    asyncio.run(check())


def test_provider_budget_resets_and_interpreter_persists_across_runs(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        provider = CodingProvider(
            [
                "```python\nx = 41\n```",
                "first done",
                "```python\nfinal_answer(x + 1)\n```",
            ]
        )
        coding = create_coding_session(
            Agent(provider),
            config=CodingConfig(
                max_provider_calls=2, observation_root=tmp_path / "artifacts"
            ),
        )
        try:
            assert (await coding.run("first")).status is RunStatus.COMPLETED
            second = await coding.run("second")
            assert second.status is RunStatus.COMPLETED
            assert second.output.content[0].value == 42
        finally:
            await coding.close()

    asyncio.run(check())


def test_python_can_catch_canonical_tool_error(tmp_path: Path) -> None:
    async def check() -> None:
        async def fail(arguments, context):
            raise ToolExecutionFailure(ToolErrorInfo("missing_file", "Missing."))

        tool = Tool(
            ToolDefinition(
                "read_file",
                "Read.",
                FrozenJsonObject(
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    }
                ),
            ),
            fail,
        )
        provider = CodingProvider(
            [
                "```python\ntry:\n    read_file()\nexcept RoboAgentToolError as e:\n    final_answer(e.code)\n```",
            ]
        )
        coding = create_coding_session(
            Agent(provider, tool_registry=ToolRegistry((tool,))),
            config=CodingConfig(observation_root=tmp_path / "artifacts"),
        )
        try:
            result = await coding.run("go")
            assert result.status is RunStatus.COMPLETED
            assert result.output.content[0].text == "missing_file"
            assert any(
                record.tool_name == "read_file" for record in result.execution_records
            )
        finally:
            await coding.close()

    asyncio.run(check())


def test_trusted_execution_is_side_effecting_and_retry_unsafe(tmp_path: Path) -> None:
    async def check() -> None:
        provider = CodingProvider(
            ["```python\nimport os\nfinal_answer(bool(os.getcwd()))\n```"]
        )
        coding = create_coding_session(
            Agent(provider),
            unsafe_python=True,
            config=CodingConfig(observation_root=tmp_path / "artifacts"),
        )
        execute = coding.session.agent.tool_registry.get("execute_python")
        assert (
            execute is not None and execute.effect_kind is ToolEffectKind.SIDE_EFFECTING
        )
        try:
            result = await coding.run("go")
            assert result.status is RunStatus.COMPLETED
            assert not result.retry_safe
            assert [item.code for item in result.retry_blockers] == [
                RetryBlockerCode.TRUSTED_EXECUTION
            ]
        finally:
            await coding.close()

    asyncio.run(check())


def test_cancellation_reaps_active_worker_generation(tmp_path: Path) -> None:
    async def check() -> None:
        provider = CodingProvider(["```python\nwhile True:\n    pass\n```"])
        coding = create_coding_session(
            Agent(provider),
            config=CodingConfig(
                execution_timeout=10, observation_root=tmp_path / "artifacts"
            ),
        )
        task = asyncio.create_task(coding.run("go"))
        for _ in range(100):
            if coding.worker.alive:
                break
            await asyncio.sleep(0.01)
        coding.cancel()
        result = await asyncio.wait_for(task, 5)
        assert result.status is RunStatus.CANCELLED
        assert not coding.worker.alive
        assert coding.worker.generation >= 2
        await coding.close()

    asyncio.run(check())


def test_trusted_worker_crash_resets_and_run_recovers(tmp_path: Path) -> None:
    async def check() -> None:
        provider = CodingProvider(
            [
                "```python\nimport os\nos._exit(7)\n```",
                "recovered",
            ]
        )
        coding = create_coding_session(
            Agent(provider),
            unsafe_python=True,
            config=CodingConfig(observation_root=tmp_path / "artifacts"),
        )
        try:
            result = await coding.run("recover")
            assert result.status is RunStatus.COMPLETED
            assert result.output.content[0].text == "recovered"
            assert coding.worker.generation == 2
            assert any(
                record.error_code == "executor_failure"
                for record in result.execution_records
            )
        finally:
            await coding.close()

    asyncio.run(check())
