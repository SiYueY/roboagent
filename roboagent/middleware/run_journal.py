"""Middleware for recording runtime run events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from roboagent.runtime.events import MemoryRunEventStore, RunEventStore
from roboagent.runtime.runs import RunManager, RunStatus


class RunJournalMiddleware(AgentMiddleware):
    """Record coarse-grained agent/model/tool runtime events."""

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_store: RunEventStore | None = None,
        run_manager: RunManager | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.event_store = event_store or MemoryRunEventStore()
        self.run_manager = run_manager

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._set_status(RunStatus.RUNNING)
        self.record("agent_start", category="trace")
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self.record("agent_end", category="trace")
        self._set_status(RunStatus.COMPLETED)
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse | AIMessage],
    ) -> ModelResponse | AIMessage:
        self.record("model_start", category="trace", metadata={"message_count": len(request.messages)})
        try:
            result = handler(request)
        except Exception as exc:
            self.record("model_error", category="trace", content=str(exc))
            self._set_status(RunStatus.FAILED, error=str(exc))
            raise
        self.record("model_end", category="trace")
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse | AIMessage]],
    ) -> ModelResponse | AIMessage:
        self.record("model_start", category="trace", metadata={"message_count": len(request.messages)})
        try:
            result = await handler(request)
        except Exception as exc:
            self.record("model_error", category="trace", content=str(exc))
            self._set_status(RunStatus.FAILED, error=str(exc))
            raise
        self.record("model_end", category="trace")
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "unknown")
        self.record("tool_start", category="trace", metadata={"tool_name": tool_name})
        try:
            result = handler(request)
        except Exception as exc:
            self.record(
                "tool_error",
                category="trace",
                content=str(exc),
                metadata={"tool_name": tool_name},
            )
            raise
        status = getattr(result, "status", "success")
        self.record("tool_end", category="trace", metadata={"tool_name": tool_name, "status": status})
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "unknown")
        self.record("tool_start", category="trace", metadata={"tool_name": tool_name})
        try:
            result = await handler(request)
        except Exception as exc:
            self.record(
                "tool_error",
                category="trace",
                content=str(exc),
                metadata={"tool_name": tool_name},
            )
            raise
        status = getattr(result, "status", "success")
        self.record("tool_end", category="trace", metadata={"tool_name": tool_name, "status": status})
        return result

    def record(
        self,
        event_type: str,
        *,
        category: str,
        content: str | dict[str, Any] = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record one event."""
        self.event_store.put(
            thread_id=self.thread_id,
            run_id=self.run_id,
            event_type=event_type,
            category=category,
            content=content,
            metadata=metadata,
        )

    def _set_status(self, status: RunStatus, *, error: str | None = None) -> None:
        if self.run_manager is None:
            return
        try:
            self.run_manager.set_status(self.run_id, status, error=error)
        except KeyError:
            return


__all__ = ["RunJournalMiddleware"]
