"""Middleware for normalizing tool execution failures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class ToolErrorHandlingMiddleware(AgentMiddleware):
    """Convert tool exceptions into stable ToolMessage errors."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except Exception as exc:
            return self._error_message(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except Exception as exc:
            return self._error_message(request, exc)

    def _error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_name = request.tool_call.get("name") or getattr(request.tool, "name", "unknown")
        tool_call_id = request.tool_call.get("id") or "unknown"
        return ToolMessage(
            content=f"Tool '{tool_name}' failed: {exc}",
            tool_call_id=tool_call_id,
            status="error",
        )


__all__ = ["ToolErrorHandlingMiddleware"]
