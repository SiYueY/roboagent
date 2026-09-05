from __future__ import annotations

import asyncio

from roboagent import Agent
from roboagent.agent import LocalSessionRepository, Session
from roboagent.context import (
    CompactingContextManager,
    CompactionPolicy,
    ContextBudget,
    MessageSegment,
    ModelContext,
    SummaryResult,
    SummarySegment,
    TokenEstimate,
)
from roboagent.mcp import MCPBinaryContent, MCPServer, MCPToolDefinition, MCPToolResult
from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    FrozenJsonObject,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from roboagent.runtime import Modality, RunStatus
from roboagent.tool import (
    ApprovalDecision,
    ApprovalResponse,
    LocalWorkspace,
    Tool,
    ToolDecision,
    ToolDefinition,
    ToolOutputLimits,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
    WorkspaceToolResultMaterializer,
    read_artifact,
)


class _ScriptedModel:
    capabilities = ModelCapabilities(
        input_modalities=frozenset({Modality.TEXT, Modality.FILE}),
        tool_calling=True,
        context_window=300,
    )

    def __init__(self, *messages: AssistantMessage) -> None:
        self._messages = iter(messages)
        self.contexts: list[ModelContext] = []

    async def stream(self, context, settings=None):
        self.contexts.append(context)
        message = next(self._messages)
        sequence = 0
        yield ResponseStarted("response", sequence)
        sequence += 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        reason = FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP
        yield ResponseCompleted(sequence, ModelResponse(message, reason))


class _RequireApproval:
    async def evaluate(self, call, tool, context):
        return ToolPolicyDecision(ToolDecision.REQUIRE_APPROVAL, "application policy")


class _Approver:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests = []

    async def request(self, request, cancellation):
        self.requests.append(request)
        return ApprovalResponse(request.approval_id, request.arguments_digest, self.decision)


def _definition(name: str = "work") -> ToolDefinition:
    return ToolDefinition(name, "Integration tool.", FrozenJsonObject({"type": "object"}))


def test_approval_configuration_flows_from_agent_through_run() -> None:
    async def scenario(decision: ApprovalDecision) -> tuple[int, int]:
        call = ToolCall("call", "work")
        model = _ScriptedModel(AssistantMessage(tool_calls=(call,)), AssistantMessage("done"))
        starts = 0

        async def handler(arguments, context):
            nonlocal starts
            starts += 1
            return ToolTextContent("worked")

        approver = _Approver(decision)
        session = Agent(
            model,
            tool_registry=ToolRegistry((Tool(_definition(), handler),)),
            tool_policy=_RequireApproval(),
            approval_provider=approver,
        ).new_session()
        result = await session.run(UserMessage("go"))
        assert result.status is RunStatus.COMPLETED
        return starts, len(approver.requests)

    assert asyncio.run(scenario(ApprovalDecision.APPROVE)) == (1, 1)
    assert asyncio.run(scenario(ApprovalDecision.REJECT)) == (0, 1)


def test_session_run_uses_its_workspace_materializer(tmp_path) -> None:
    async def check() -> None:
        call = ToolCall("call", "work")
        workspace = LocalWorkspace(tmp_path / "workspace")
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4),
        )
        model = _ScriptedModel(AssistantMessage(tool_calls=(call,)), AssistantMessage("done"))
        session = Agent(
            model,
            tool_registry=ToolRegistry((Tool(_definition(), lambda arguments, context: ToolTextContent("large result")),)),
        ).new_session(workspace=workspace, result_materializer=materializer)

        assert (await session.run(UserMessage("go"))).status is RunStatus.COMPLETED
        result_message = next(message for message in session.messages if isinstance(message, ToolResultMessage))
        artifact = result_message.content[0]
        assert isinstance(artifact, ArtifactReferenceContent)
        assert await read_artifact(workspace, artifact) == b"large result"

    asyncio.run(check())


def test_persisted_tool_artifact_survives_session_restore(tmp_path) -> None:
    async def check() -> None:
        call = ToolCall("call", "work")
        repository = LocalSessionRepository(tmp_path / "sessions")
        workspace = LocalWorkspace(tmp_path / "workspace")
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4),
        )
        initial_model = _ScriptedModel(
            AssistantMessage(tool_calls=(call,)), AssistantMessage("first run complete")
        )
        session = Agent(
            initial_model,
            tool_registry=ToolRegistry((Tool(_definition(), lambda arguments, context: ToolTextContent("durable result")),)),
        ).new_session(
            session_id="durable",
            repository=repository,
            workspace=workspace,
            result_materializer=materializer,
        )
        assert (await session.run(UserMessage("go"))).status is RunStatus.COMPLETED

        snapshot = await repository.load("durable")
        assert snapshot is not None
        resume_model = _ScriptedModel(AssistantMessage("resumed"))
        reopened = LocalWorkspace(tmp_path / "workspace")
        restored = Session.restore(
            agent=Agent(resume_model),
            snapshot=snapshot,
            repository=repository,
            workspace=reopened,
            result_materializer=WorkspaceToolResultMaterializer(
                workspace=reopened,
                limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4),
            ),
        )
        assert (await restored.run()).status is RunStatus.COMPLETED
        tool_message = next(
            message for message in restored.messages if isinstance(message, ToolResultMessage)
        )
        artifact = tool_message.content[0]
        assert isinstance(artifact, ArtifactReferenceContent)
        assert await read_artifact(reopened, artifact) == b"durable result"
        assert any(
            isinstance(segment, MessageSegment)
            and isinstance(segment.message, ToolResultMessage)
            and isinstance(segment.message.content[0], ArtifactReferenceContent)
            for segment in resume_model.contexts[0].segments
        )

    asyncio.run(check())


def test_mcp_tool_crosses_approval_materialization_and_transcript_pipeline(tmp_path) -> None:
    async def check() -> None:
        class Client:
            async def connect(self):
                pass

            async def list_tools(self):
                return (MCPToolDefinition("remote", "Remote.", FrozenJsonObject({"type": "object"})),)

            async def call_tool(self, name, arguments, cancellation):
                return MCPToolResult((MCPBinaryContent(b"remote bytes", "application/octet-stream"),))

            async def close(self):
                pass

        workspace = LocalWorkspace(tmp_path / "workspace")
        materializer = WorkspaceToolResultMaterializer(
            workspace=workspace,
            limits=ToolOutputLimits(max_raw_bytes=1000, max_inline_bytes=4),
        )
        approver = _Approver(ApprovalDecision.APPROVE)
        call = ToolCall("call", "remote")
        model = _ScriptedModel(AssistantMessage(tool_calls=(call,)), AssistantMessage("done"))
        async with MCPServer(Client()) as server:
            agent = Agent(
                model,
                tool_registry=ToolRegistry(await server.tools()),
                tool_policy=_RequireApproval(),
                approval_provider=approver,
            )
            session = agent.new_session(workspace=workspace, result_materializer=materializer)
            assert (await session.run(UserMessage("go"))).status is RunStatus.COMPLETED
        tool_message = next(message for message in session.messages if isinstance(message, ToolResultMessage))
        assert isinstance(tool_message.content[0], ArtifactReferenceContent)
        assert len(approver.requests) == 1

    asyncio.run(check())


def test_compaction_persists_restores_and_continues_incrementally(tmp_path) -> None:
    async def check() -> None:
        class Estimator:
            def estimate(self, context):
                total = 10 + len(context.system_prompt or "")
                for segment in context.segments:
                    if isinstance(segment, SummarySegment):
                        total += len(segment.text)
                    elif isinstance(segment, MessageSegment):
                        total += sum(
                            len(content.text)
                            for content in segment.message.content
                            if isinstance(content, TextContent)
                        )
                return TokenEstimate(total, exact=True)

        class Summarizer:
            def __init__(self) -> None:
                self.calls = []

            async def summarize(self, *, existing_summary, messages, cancellation):
                self.calls.append((existing_summary, messages))
                return SummaryResult(f"summary-{len(self.calls)}")

        summarizer = Summarizer()

        def manager():
            return CompactingContextManager(
                budget=ContextBudget(230),
                estimator=Estimator(),
                summarizer=summarizer,
                policy=CompactionPolicy(target_ratio=1),
                provider_default_reserve=0,
            )

        repository = LocalSessionRepository(tmp_path / "sessions")
        messages = (
            UserMessage("a" * 40),
            AssistantMessage("b" * 40),
            UserMessage("latest"),
        )
        first = Agent(_ScriptedModel(AssistantMessage("done")), context_manager=manager()).new_session(
            messages=messages,
            session_id="compaction",
            repository=repository,
        )
        assert (await first.run()).status is RunStatus.COMPLETED
        assert first.current_compaction is not None

        snapshot = await repository.load("compaction")
        assert snapshot is not None and snapshot.compaction is not None
        restored = Session.restore(
            agent=Agent(_ScriptedModel(AssistantMessage("continued")), context_manager=manager()),
            snapshot=snapshot,
            repository=repository,
        )
        assert (await restored.run(UserMessage("n" * 50))).status is RunStatus.COMPLETED
        assert len(summarizer.calls) == 2
        assert summarizer.calls[1][0] == snapshot.compaction
        latest = await repository.load("compaction")
        assert latest is not None and latest.compaction is not None
        assert latest.compaction.source_end_exclusive > snapshot.compaction.source_end_exclusive

    asyncio.run(check())
