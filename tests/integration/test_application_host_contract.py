"""Application-Host contract test.

`roserver` may only use roboagent's public API (document §3.3 / §4.3). This test
acts as that host: it drives a full Session lifecycle with a host-supplied
session id, an approval-gated side-effecting Tool, durable persistence, restart
re-open, pending recovery and deletion — using public imports only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from roboagent import Agent, Session
from roboagent.agent import (
    LocalSessionRepository,
    SessionNotFoundError,
)
from roboagent.context import PromptInput
from roboagent.message import (
    AssistantMessage,
    FrozenJsonObject,
    TextContent,
    ToolCall,
    UserMessage,
)
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ResponseCompleted,
    ResponseStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
)
from roboagent.runtime import Modality, RunStatus
from roboagent.tool import (
    ApprovalDecision,
    ApprovalResponse,
    ApprovalSettings,
    Tool,
    ToolDecision,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
)


class ScriptedModel:
    capabilities = ModelCapabilities(
        frozenset({Modality.TEXT}), frozenset({Modality.TEXT}), True, True
    )

    def __init__(self, replies: tuple[AssistantMessage, ...]) -> None:
        self.replies = iter(replies)

    async def stream(self, context, settings=None):
        message = next(self.replies)
        sequence = 0
        yield ResponseStarted("response", sequence)
        sequence += 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        for content in message.content:
            if isinstance(content, TextContent):
                yield TextDelta(sequence, content.text)
                sequence += 1
        reason = FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP
        yield ResponseCompleted(sequence, ModelResponse(message, reason))


class RequireApprovalPolicy:
    """Host policy: side-effecting robot commands need operator approval."""

    async def evaluate(self, call, tool, context):
        if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
            return ToolPolicyDecision(
                ToolDecision.REQUIRE_APPROVAL, reason="robot command"
            )
        return ToolPolicyDecision(ToolDecision.ALLOW)


class HostApprovalProvider:
    """Async, in-process provider of the kind roserver implements."""

    def __init__(self, decision: ApprovalDecision = ApprovalDecision.APPROVE) -> None:
        self.decision = decision
        self.requests: list[object] = []
        self.responses: list[ApprovalResponse] = []

    async def request(self, request, cancellation):
        self.requests.append(request)
        response = ApprovalResponse(
            request.approval_id, request.arguments_digest, self.decision
        )
        self.responses.append(response)
        return response


def _registry() -> ToolRegistry:
    async def navigate(arguments, context):
        return ToolTextContent("moved")

    return ToolRegistry(
        (
            Tool(
                ToolDefinition(
                    "navigate",
                    "Move the robot.",
                    FrozenJsonObject(
                        {
                            "type": "object",
                            "properties": {"target": {"type": "string"}},
                            "required": ["target"],
                            "additionalProperties": False,
                        }
                    ),
                ),
                navigate,
                ToolExecutionMode.CONCURRENT,
                ToolEffectKind.SIDE_EFFECTING,
            ),
        )
    )


def _host(
    provider: HostApprovalProvider,
    session_id: str,
    repository: LocalSessionRepository,
) -> tuple[Agent, Session]:
    call = ToolCall("call-1", "navigate", FrozenJsonObject({"target": "meeting_room"}))
    agent = Agent(
        ScriptedModel(
            (AssistantMessage(tool_calls=(call,)), AssistantMessage("arrived"))
        ),
        tool_registry=_registry(),
        prompt=PromptInput("Operate safely."),
        tool_policy=RequireApprovalPolicy(),
        approval_provider=provider,
        approval_settings=ApprovalSettings(timeout=30.0),
    )
    session = agent.new_session(session_id=session_id, repository=repository)
    return agent, session


def test_application_host_full_lifecycle_uses_public_api_only(tmp_path: Path) -> None:
    async def check() -> None:
        repository = LocalSessionRepository(tmp_path / "sessions")
        provider = HostApprovalProvider()
        session_id = "sess_host"
        agent, session = _host(provider, session_id, repository)

        assert session.session_id == session_id
        run = session.start(UserMessage("go to the meeting room"))
        subscription = run.subscribe()
        result = await run.result()
        events = [event async for event in subscription]

        # 1. The Run completed and the gated Tool actually executed.
        assert result.status is RunStatus.COMPLETED
        effects = [item for item in result.effects if item.call_id == "call-1"]
        assert len(effects) == 1
        assert effects[0].transcript_committed

        # 2. The host provider saw one approval and answered it itself.
        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.tool_call_id == "call-1"
        assert request.session_id == session_id
        assert request.effect_capability == "side_effecting"
        assert provider.responses[0].decision is ApprovalDecision.APPROVE

        # 3. Approval and effect are observable through public Runtime events.
        types = [event.type for event in events]
        assert "approval.requested" in types
        assert "approval.resolved" in types
        committed = next(event for event in events if event.type == "tool_batch.committed")
        assert committed.payload["effects"][0]["tool_call_id"] == "call-1"
        assert committed.payload["effects"][0]["effect_status"] == "succeeded"

        # 4. Host-visible transcript carries stable canonical identities.
        live_ids = [message.message_id for message in session.messages]
        assert len(live_ids) == len(set(live_ids))
        assert live_ids

        # 5. Restart recovery: reopen by id through the public helper.
        reopened = await Session.open(
            agent=agent, session_id=session_id, repository=repository
        )
        assert [message.message_id for message in reopened.messages] == live_ids

        # 6. Pending recovery is host-drivable and durable.
        await reopened.steer(UserMessage("stop"))
        assert await reopened.pending_inputs()
        await reopened.clear_pending()
        reloaded = await repository.load(session_id)
        assert reloaded is not None and reloaded.pending == ()

        # 7. Deletion is a host-level operation, and repeated delete is a no-op.
        await reopened.delete()
        assert await repository.load(session_id) is None
        await reopened.delete()
        try:
            await Session.open(
                agent=agent, session_id=session_id, repository=repository
            )
        except SessionNotFoundError:
            pass
        else:  # pragma: no cover - explicit failure
            raise AssertionError("deleted Session must not be openable")

    asyncio.run(check())


def test_host_supplied_session_id_is_respected_and_persisted(tmp_path: Path) -> None:
    async def check() -> None:
        repository = LocalSessionRepository(tmp_path / "sessions")
        provider = HostApprovalProvider()
        _, session = _host(provider, "host-chosen-id", repository)
        run = session.start(UserMessage("go"))
        await run.result()

        snapshot = await repository.load("host-chosen-id")
        assert snapshot is not None
        assert snapshot.session_id == "host-chosen-id"

    asyncio.run(check())
