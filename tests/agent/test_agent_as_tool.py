from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from roboagent.agent import Agent, RunConfig, Session
from roboagent.agent.delegation import ChildLifecycleError, promote_child_output
from roboagent.context import ContextSummary
from roboagent.message import (
    ArtifactReferenceContent,
    AssistantMessage,
    AudioContent,
    BytesSource,
    FileContent,
    FileSource,
    FrozenJsonObject,
    ImageContent,
    JsonContent,
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
    Usage,
    UsageUpdated,
)
from roboagent.runtime import Modality, RunStatus, RuntimeCancellation
from roboagent.tool import (
    ApprovalDecision,
    ApprovalResponse,
    InMemoryWorkspace,
    Tool,
    ToolDecision,
    ToolDefinition,
    ToolEffectKind,
    ToolPolicyDecision,
    ToolRegistry,
    ToolTextContent,
    WorkspaceArtifactDestination,
    WorkspaceArtifactReader,
)


class ScriptedModel:
    capabilities = ModelCapabilities(
        input_modalities=frozenset(Modality),
        output_modalities=frozenset(Modality),
        tool_calling=True,
    )

    def __init__(self, *messages: AssistantMessage) -> None:
        self.messages = iter(messages)

    async def stream(self, context, settings=None):
        message = next(self.messages)
        yield ResponseStarted("response", 0)
        sequence = 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        usage = Usage(1, 1, 2)
        yield UsageUpdated(sequence, usage)
        sequence += 1
        yield ResponseCompleted(
            sequence,
            ModelResponse(
                message,
                FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP,
                usage,
            ),
        )


def test_agent_as_tool_child_run_shares_tree_without_duplicate_effect_or_usage() -> (
    None
):
    async def check() -> None:
        child_call = ToolCall("child-write", "write")
        child = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(child_call,)),
                AssistantMessage("child answer"),
            ),
            tool_registry=ToolRegistry(
                (
                    Tool(
                        ToolDefinition(
                            "write", "Write.", FrozenJsonObject({"type": "object"})
                        ),
                        lambda arguments, context: ToolTextContent("changed"),
                        effect_kind=ToolEffectKind.SIDE_EFFECTING,
                    ),
                )
            ),
        )
        child_tool = child.as_tool(name="delegate", description="Delegate work.")
        root_call = ToolCall("delegate-call", "delegate", {"task": "do it"})
        root = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(root_call,)),
                AssistantMessage("root answer"),
            ),
            tool_registry=ToolRegistry((child_tool,)),
        )
        session = root.new_session()
        run = session.start(UserMessage("start"))
        subscription = run.subscribe()
        result = await run.result()
        events = [event async for event in subscription]

        assert result.status is RunStatus.COMPLETED
        assert result.usage == Usage(4, 4, 8)
        assert len(result.effects) == 1
        assert result.effects[0].call_id == "child-write"
        assert result.effects[0].transcript_committed
        assert [record.tool_name for record in result.execution_records] == [
            "write",
            "delegate",
        ]
        assert sum(event.type == "child_run.started" for event in events) == 1
        assert sum(event.type == "child_run.completed" for event in events) == 1
        child_event = next(
            event for event in events if event.type == "child_run.started"
        )
        assert child_event.lineage.agent_depth == 1
        assert child_event.lineage.agent_tool_name == "delegate"
        exchange = next(
            message
            for message in session.messages
            if isinstance(message, ToolResultMessage)
        )
        assert exchange.content[0].text == "child answer"

    asyncio.run(check())


class RecordingFactory:
    def __init__(self, make_session=None, error=None) -> None:
        self.make_session = make_session
        self.error = error
        self.parent = None
        self.session = None

    async def create(self, *, parent, agent):
        self.parent = parent
        if self.error is not None:
            raise self.error
        self.session = self.make_session(agent)
        return self.session


async def _run_delegation(child: Agent, factory=None):
    tool = child.as_tool(
        name="delegate", description="Delegate.", session_factory=factory
    )
    root = Agent(
        ScriptedModel(
            AssistantMessage(
                tool_calls=(ToolCall("call", "delegate", {"task": "work"}),)
            ),
            AssistantMessage("root done"),
        ),
        tool_registry=ToolRegistry((tool,)),
    )
    session = root.new_session(session_id="root-session")
    result = await session.run(UserMessage("go"))
    delegate = next(
        record for record in result.execution_records if record.tool_name == "delegate"
    )
    return result, delegate


def test_child_factory_failure_valid_and_invalid_session_ownership() -> None:
    async def check() -> None:
        child = Agent(ScriptedModel(AssistantMessage("child")))

        failed_factory = RecordingFactory(error=RuntimeError("factory failed"))
        _result, record = await _run_delegation(child, failed_factory)
        assert record.error_code == "child_session_creation_failed"
        assert failed_factory.session is None

        valid_factory = RecordingFactory(lambda agent: agent.new_session())
        completed, record = await _run_delegation(child, valid_factory)
        assert record.error_code is None
        assert completed.status is RunStatus.COMPLETED
        assert valid_factory.session.closed
        assert valid_factory.parent.root_session_id == "root-session"
        assert valid_factory.session.repository is None

        invalid_factory = RecordingFactory(
            lambda agent: agent.new_session((UserMessage("not isolated"),))
        )
        _result, record = await _run_delegation(
            Agent(ScriptedModel(AssistantMessage("unused"))), invalid_factory
        )
        assert record.error_code == "invalid_child_session"
        assert invalid_factory.session.closed

        wrong = Agent(ScriptedModel(AssistantMessage("wrong")))
        wrong_factory = RecordingFactory(lambda _agent: wrong.new_session())
        _result, record = await _run_delegation(
            Agent(ScriptedModel(AssistantMessage("unused"))), wrong_factory
        )
        assert record.error_code == "invalid_child_session"
        assert wrong_factory.session.closed

        closed_session = Agent(ScriptedModel(AssistantMessage("unused"))).new_session()
        await closed_session.close()
        closed_factory = RecordingFactory(lambda _agent: closed_session)
        _result, record = await _run_delegation(closed_session.agent, closed_factory)
        assert record.error_code == "invalid_child_session"

        pending_agent = Agent(ScriptedModel(AssistantMessage("unused")))
        pending_session = pending_agent.new_session()
        await pending_session.follow_up(UserMessage("queued"))
        pending_factory = RecordingFactory(lambda _agent: pending_session)
        _result, record = await _run_delegation(pending_agent, pending_factory)
        assert record.error_code == "invalid_child_session"
        assert pending_session.closed

        compacted_agent = Agent(ScriptedModel(AssistantMessage("unused")))
        compacted_session = compacted_agent.new_session()
        compacted_session._current_compaction = ContextSummary(
            0, 0, "empty", "historical context", 1
        )
        compacted_factory = RecordingFactory(lambda _agent: compacted_session)
        _result, record = await _run_delegation(compacted_agent, compacted_factory)
        assert record.error_code == "invalid_child_session"
        assert compacted_session.closed

    asyncio.run(check())


class FailingCloseSession(Session):
    async def close(self):
        await super().close()
        raise RuntimeError("close failed")


class TrackingCloseSession(Session):
    close_attempts = 0

    async def close(self):
        self.close_attempts += 1
        return await super().close()


def test_completed_child_close_failure_fails_outer_invocation() -> None:
    async def check() -> None:
        child = Agent(ScriptedModel(AssistantMessage("child")))
        factory = RecordingFactory(lambda agent: FailingCloseSession(agent))
        result, delegate = await _run_delegation(child, factory)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code == "child_cleanup_failed"
        assert factory.session.closed

    asyncio.run(check())


class PromotionOrderSession(Session):
    promoted_before_close = False

    async def close(self):
        entries = await self.workspace.list("blobs")
        self.promoted_before_close = any(
            entry.path.startswith("blobs/sha256/") for entry in entries
        )
        return await super().close()


class PromotionOrderFactory:
    def __init__(self) -> None:
        self.session = None

    async def create(self, *, parent, agent):
        self.session = PromotionOrderSession(
            agent,
            workspace=parent.workspace,
            result_materializer=parent.materializer,
            artifact_reader=parent.artifact_reader,
            artifact_destination=parent.artifact_destination,
        )
        return self.session


def test_required_artifact_promotion_finishes_before_child_session_close() -> None:
    async def check() -> None:
        child = Agent(
            ScriptedModel(
                AssistantMessage(
                    (FileContent(BytesSource(b"artifact"), "application/octet-stream"),)
                )
            )
        )
        factory = PromotionOrderFactory()
        result, delegate = await _run_delegation(child, factory)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code is None
        assert factory.session.closed
        assert factory.session.promoted_before_close

    asyncio.run(check())


def test_active_invalid_child_session_is_still_offered_to_close() -> None:
    async def check() -> None:
        child = Agent(ScriptedModel(AssistantMessage("unused")))
        session = TrackingCloseSession(child)
        session._active_run_id = "foreign-run"
        factory = RecordingFactory(lambda _agent: session)
        _result, delegate = await _run_delegation(child, factory)
        assert delegate.error_code == "invalid_child_session"
        assert session.close_attempts == 1
        session._active_run_id = None
        await session.close()

    asyncio.run(check())


class BrokenSkillManager:
    def bind_run(self, run_id):
        raise RuntimeError("cannot bind")


def test_child_run_start_failure_has_distinct_error_mapping() -> None:
    async def check() -> None:
        child = Agent(
            ScriptedModel(AssistantMessage("unused")),
            skill_manager=BrokenSkillManager(),
        )
        result, delegate = await _run_delegation(child)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code == "child_run_start_failed"

    asyncio.run(check())


def test_child_failure_retains_nested_effects_without_outer_effect() -> None:
    async def check() -> None:
        child = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(ToolCall("child-write", "write", {}),))
            ),
            tool_registry=ToolRegistry(
                (
                    Tool(
                        ToolDefinition(
                            "write", "Write.", FrozenJsonObject({"type": "object"})
                        ),
                        lambda arguments, context: ToolTextContent("changed"),
                        effect_kind=ToolEffectKind.SIDE_EFFECTING,
                    ),
                )
            ),
        )
        result, delegate = await _run_delegation(child)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code == "child_execution_failed"
        assert [effect.call_id for effect in result.effects] == ["child-write"]
        assert all(effect.call_id != "call" for effect in result.effects)

    asyncio.run(check())


class UnsettledResource:
    async def close(self) -> None:
        raise RuntimeError("close failed")

    async def force_close(self) -> None:
        raise RuntimeError("force close failed")


def test_child_resource_cleanup_failure_fails_outer_and_blocks_retry() -> None:
    async def check() -> None:
        async def register(arguments, context):
            context.execution.register_resource(UnsettledResource())
            return ToolTextContent("registered")

        child = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(ToolCall("leaf", "resource", {}),)),
                AssistantMessage("child done"),
            ),
            tool_registry=ToolRegistry(
                (
                    Tool(
                        ToolDefinition(
                            "resource",
                            "Register resource.",
                            FrozenJsonObject({"type": "object"}),
                        ),
                        register,
                    ),
                )
            ),
            default_run_config=RunConfig(cleanup_timeout=0.01),
        )
        result, delegate = await _run_delegation(child)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code == "child_cleanup_failed"
        assert not result.retry_safe
        assert any(
            blocker.code.value == "cleanup_uncertain"
            for blocker in result.retry_blockers
        )

    asyncio.run(check())


class CancellingHook:
    async def on_run_start(self, context) -> None:
        context.run_context.cancellation.cancel()


def test_external_child_cancellation_maps_to_child_cancelled() -> None:
    async def check() -> None:
        child = Agent(
            ScriptedModel(AssistantMessage("unreached")), hooks=(CancellingHook(),)
        )
        result, delegate = await _run_delegation(child)
        assert result.status is RunStatus.COMPLETED
        assert delegate.error_code == "child_cancelled"
        assert result.effects == ()

    asyncio.run(check())


class BlockingModel:
    capabilities = ModelCapabilities(tool_calling=True)

    async def stream(self, context, settings=None):
        yield ResponseStarted("blocked", 0)
        await asyncio.Event().wait()


def test_parent_cancellation_propagates_through_child_without_error_mapping() -> None:
    async def check() -> None:
        child = Agent(BlockingModel())
        tool = child.as_tool(name="delegate", description="Delegate.")
        root = Agent(
            ScriptedModel(
                AssistantMessage(
                    tool_calls=(ToolCall("call", "delegate", {"task": "wait"}),)
                )
            ),
            tool_registry=ToolRegistry((tool,)),
        )
        run = root.new_session().start(UserMessage("go"))
        subscription = run.subscribe()
        async for event in subscription:
            if event.type == "child_run.started":
                run.cancel()
                break
        result = await run.result()
        assert result.status is RunStatus.CANCELLED
        assert result.effects == ()

    asyncio.run(check())


class RequiringApproval:
    async def evaluate(self, call, tool, context):
        return ToolPolicyDecision(ToolDecision.REQUIRE_APPROVAL, "explicit")


class RecordingApprover:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request, cancellation):
        self.requests.append(request)
        return ApprovalResponse(
            request.approval_id, request.arguments_digest, ApprovalDecision.APPROVE
        )


def test_parent_agent_tool_and_child_leaf_require_independent_approval() -> None:
    async def check() -> None:
        child_approver = RecordingApprover()
        child = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(ToolCall("leaf", "write", {}),)),
                AssistantMessage("child done"),
            ),
            tool_registry=ToolRegistry(
                (
                    Tool(
                        ToolDefinition(
                            "write", "Write.", FrozenJsonObject({"type": "object"})
                        ),
                        lambda arguments, context: ToolTextContent("changed"),
                        effect_kind=ToolEffectKind.SIDE_EFFECTING,
                    ),
                )
            ),
            tool_policy=RequiringApproval(),
            approval_provider=child_approver,
        )
        parent_approver = RecordingApprover()
        root = Agent(
            ScriptedModel(
                AssistantMessage(
                    tool_calls=(
                        ToolCall("outer", "delegate", {"task": "perform work"}),
                    )
                ),
                AssistantMessage("root done"),
            ),
            tool_registry=ToolRegistry(
                (child.as_tool(name="delegate", description="Delegate."),)
            ),
            tool_policy=RequiringApproval(),
            approval_provider=parent_approver,
        )
        result = await root.new_session().run(UserMessage("go"))
        assert result.status is RunStatus.COMPLETED
        assert [request.tool_name for request in parent_approver.requests] == [
            "delegate"
        ]
        assert [request.tool_name for request in child_approver.requests] == ["write"]

    asyncio.run(check())


class MemoryWriter:
    def __init__(self, destination, media_type) -> None:
        self.destination = destination
        self.media_type = media_type
        self.data = bytearray()
        self.aborted = False

    async def write(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        if self.destination.cancel_after_write:
            self.destination.cancellation.cancel()

    async def publish(self) -> ArtifactReferenceContent:
        data = bytes(self.data)
        digest = hashlib.sha256(data).hexdigest()
        self.destination.published.append(data)
        return ArtifactReferenceContent(
            f"workspace://blobs/{len(self.destination.published)}-{digest}",
            self.media_type,
            len(data),
            f"sha256:{digest}",
        )

    async def abort(self) -> None:
        self.aborted = True


class MemoryDestination:
    def __init__(self, cancellation, *, cancel_after_write=False) -> None:
        self.cancellation = cancellation
        self.cancel_after_write = cancel_after_write
        self.writers = []
        self.published = []

    async def create_temp(self, *, media_type):
        writer = MemoryWriter(self, media_type)
        self.writers.append(writer)
        return writer


class ChunkReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.chunks = 0

    async def iter_bytes(self, reference, *, chunk_size):
        for offset in range(0, len(self.data), 2):
            self.chunks += 1
            yield self.data[offset : offset + 2]


def test_child_output_streaming_promotion_for_media_and_reference(tmp_path) -> None:
    async def check() -> None:
        file_path = Path(tmp_path / "file.bin")
        file_path.write_bytes(b"file")
        source = b"artifact"
        digest = f"sha256:{hashlib.sha256(source).hexdigest()}"
        reference = ArtifactReferenceContent(
            "workspace://blobs/source", None, len(source), digest
        )
        cancellation = RuntimeCancellation()
        destination = MemoryDestination(cancellation)
        reader = ChunkReader(source)
        output = AssistantMessage(
            (
                TextContent("text"),
                JsonContent({"ok": True}),
                ImageContent(BytesSource(b"image"), "image/png"),
                AudioContent(BytesSource(b"audio"), "audio/mpeg"),
                FileContent(FileSource(str(file_path)), "application/octet-stream"),
                reference,
            )
        )
        promoted = await promote_child_output(
            output,
            reader=reader,
            destination=destination,
            cancellation=cancellation,
            max_bytes=1024,
            chunk_size=2,
        )
        assert promoted.content[:2] == output.content[:2]
        assert all(
            isinstance(item, ArtifactReferenceContent) for item in promoted.content[2:]
        )
        assert promoted.content[-1].uri != reference.uri
        assert destination.published == [b"image", b"audio", b"file", source]
        assert reader.chunks > 1

    asyncio.run(check())


def test_child_artifact_promotion_size_digest_and_cancellation() -> None:
    async def check() -> None:
        source = b"abcd"
        bad = ArtifactReferenceContent(
            "workspace://blobs/source",
            None,
            len(source),
            f"sha256:{hashlib.sha256(b'other').hexdigest()}",
        )
        cancellation = RuntimeCancellation()
        destination = MemoryDestination(cancellation)
        with pytest.raises(ChildLifecycleError, match="digest") as mismatch:
            await promote_child_output(
                AssistantMessage((bad,)),
                reader=ChunkReader(source),
                destination=destination,
                cancellation=cancellation,
                max_bytes=10,
            )
        assert mismatch.value.code == "child_artifact_digest_mismatch"
        assert destination.writers[0].aborted and not destination.published

        too_large = RuntimeCancellation()
        destination = MemoryDestination(too_large)
        with pytest.raises(ChildLifecycleError) as oversized:
            await promote_child_output(
                AssistantMessage((bad,)),
                reader=ChunkReader(source),
                destination=destination,
                cancellation=too_large,
                max_bytes=3,
            )
        assert oversized.value.code == "child_artifact_too_large"
        assert destination.writers[0].aborted

        cancelled = RuntimeCancellation()
        destination = MemoryDestination(cancelled, cancel_after_write=True)
        with pytest.raises(asyncio.CancelledError):
            await promote_child_output(
                AssistantMessage((bad,)),
                reader=ChunkReader(source),
                destination=destination,
                cancellation=cancelled,
                max_bytes=10,
            )
        assert destination.writers[0].aborted and not destination.published

    asyncio.run(check())


def test_default_artifact_reader_leaves_integrity_mapping_to_promotion() -> None:
    async def check() -> None:
        workspace = InMemoryWorkspace()
        await workspace.write("blobs/source", b"corrupt")
        destination = WorkspaceArtifactDestination(workspace)

        wrong_size = ArtifactReferenceContent(
            "workspace://blobs/source",
            "application/octet-stream",
            len(b"expected"),
            f"sha256:{hashlib.sha256(b'expected').hexdigest()}",
        )
        with pytest.raises(ChildLifecycleError) as caught:
            await promote_child_output(
                AssistantMessage((wrong_size,)),
                reader=WorkspaceArtifactReader(workspace),
                destination=destination,
                cancellation=RuntimeCancellation(),
                max_bytes=1024,
            )
        assert caught.value.code == "child_output_materialization_failed"

        wrong_digest = ArtifactReferenceContent(
            "workspace://blobs/source",
            "application/octet-stream",
            len(b"corrupt"),
            f"sha256:{hashlib.sha256(b'expected').hexdigest()}",
        )
        with pytest.raises(ChildLifecycleError) as caught:
            await promote_child_output(
                AssistantMessage((wrong_digest,)),
                reader=WorkspaceArtifactReader(workspace),
                destination=destination,
                cancellation=RuntimeCancellation(),
                max_bytes=1024,
            )
        assert caught.value.code == "child_artifact_digest_mismatch"

    asyncio.run(check())


def test_agent_tool_promotes_media_and_enforces_effective_child_run_limit() -> None:
    async def check() -> None:
        child = Agent(
            ScriptedModel(
                AssistantMessage((ImageContent(BytesSource(b"image"), "image/png"),))
            )
        )
        tool = child.as_tool(name="delegate", description="Delegate.")
        root = Agent(
            ScriptedModel(
                AssistantMessage(
                    tool_calls=(ToolCall("call", "delegate", {"task": "image"}),)
                ),
                AssistantMessage("done"),
            ),
            tool_registry=ToolRegistry((tool,)),
        )
        session = root.new_session()
        result = await session.run(UserMessage("go"))
        assert result.status is RunStatus.COMPLETED
        tool_result = next(
            message
            for message in session.messages
            if isinstance(message, ToolResultMessage)
        )
        assert isinstance(tool_result.content[0], ArtifactReferenceContent)

        limited_child = Agent(
            ScriptedModel(
                AssistantMessage((ImageContent(BytesSource(b"image"), "image/png"),))
            ),
            default_run_config=RunConfig(max_child_artifact_bytes=4),
        )
        _result, delegate = await _run_delegation(limited_child)
        assert delegate.error_code == "child_artifact_too_large"

    asyncio.run(check())


def test_agent_as_tool_rejects_blank_task_and_has_static_child_effect_bound() -> None:
    child = Agent(
        ScriptedModel(AssistantMessage("unused")),
        tool_registry=ToolRegistry(
            (
                Tool(
                    ToolDefinition(
                        "write", "Write.", FrozenJsonObject({"type": "object"})
                    ),
                    lambda arguments, context: ToolTextContent("ok"),
                    effect_kind=ToolEffectKind.SIDE_EFFECTING,
                ),
            )
        ),
    )
    tool = child.as_tool(name="delegate", description="Delegate.")
    assert tool.effect_kind is ToolEffectKind.SIDE_EFFECTING

    async def check() -> None:
        root_call = ToolCall("call", "delegate", {"task": "   "})
        root = Agent(
            ScriptedModel(
                AssistantMessage(tool_calls=(root_call,)), AssistantMessage("done")
            ),
            tool_registry=ToolRegistry((tool,)),
        )
        result = await root.new_session().run(UserMessage("go"))
        assert result.status is RunStatus.COMPLETED
        assert result.execution_records[0].error_code == "invalid_arguments"

    asyncio.run(check())
