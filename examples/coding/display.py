"""Rich terminal presentation adapted from smolagents CLI/monitoring patterns.

Presentation ideas were reviewed from ``smolagents/cli.py`` and
``smolagents/monitoring.py`` at commit
30bb1161095dbae2271e6bc3cc4c219cc3897a57 (Apache-2.0).  This module consumes
only canonical RoboAgent events, approval requests, transcripts, and results.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from roboagent.message import JsonContent, TextContent, ToolResultMessage, thaw_json
from roboagent.runtime import AgentEvent
from roboagent.tool import ApprovalDecision, ApprovalRequest, ApprovalResponse


@dataclass(slots=True)
class CodingDisplay:
    console: Console = field(default_factory=Console)
    _argument_buffers: dict[str, str] = field(default_factory=dict, init=False)

    async def consume(self, subscription, session) -> None:
        async for event in subscription:
            self.event(event, session)

    def event(self, event: AgentEvent, session) -> None:
        payload = event.payload
        if event.type == "model.delta":
            self.console.print(str(payload.get("text", "")), end="", markup=False)
        elif event.type == "model.tool_call_started":
            call_id = payload.get("tool_call_id")
            if isinstance(call_id, str):
                self._argument_buffers[call_id] = ""
        elif event.type == "model.tool_call_arguments_delta":
            call_id, delta = payload.get("tool_call_id"), payload.get("delta")
            if isinstance(call_id, str) and isinstance(delta, str):
                self._argument_buffers[call_id] = (
                    self._argument_buffers.get(call_id, "") + delta
                )
        elif event.type == "model.tool_call_completed":
            self._render_call(event)
        elif event.type == "tool.started":
            self.console.print(
                f"\n[cyan]Tool[/cyan] {payload.get('tool_name')} started"
            )
        elif event.type == "tool.failed":
            self.console.print(
                f"[red]Tool {payload.get('tool_name')} failed:[/red] {payload.get('error_code')}"
            )
        elif event.type == "tool_batch.committed":
            self.render_latest_observations(session.messages)
        elif event.type in {"run.completed", "run.failed", "run.cancelled"}:
            self.console.print()

    def _render_call(self, event: AgentEvent) -> None:
        call_id = event.payload.get("tool_call_id")
        name = event.payload.get("tool_name")
        raw = (
            self._argument_buffers.pop(call_id, "") if isinstance(call_id, str) else ""
        )
        if name == "execute_python":
            try:
                decoded = json.loads(raw)
                code = decoded.get("code", "") if isinstance(decoded, dict) else ""
            except ValueError:
                code = raw
            self.console.print(
                Panel(
                    Syntax(str(code), "python", word_wrap=True), title="Python action"
                )
            )
        else:
            self.console.print(Panel(raw[:4096], title=f"Tool call: {name}"))

    def render_latest_observations(self, messages: tuple[object, ...]) -> None:
        for message in reversed(messages):
            if not isinstance(message, ToolResultMessage):
                break
            for content in message.content:
                if (
                    isinstance(content, JsonContent)
                    and message.tool_name == "execute_python"
                ):
                    envelope = thaw_json(content.value)
                    if isinstance(envelope, dict):
                        observation = envelope.get("observation", "")
                        artifact = envelope.get("observation_file")
                        body = str(observation)
                        if artifact:
                            body += f"\n\nFull observation: {artifact}"
                        if body or envelope.get("is_final") is not True:
                            self.console.print(
                                Panel(Text(body), title="Python observation")
                            )
                elif isinstance(content, TextContent):
                    self.console.print(
                        Panel(
                            Text(content.text), title=f"{message.tool_name} observation"
                        )
                    )

    def result(self, result) -> None:
        table = Table(title="Run result", show_header=False)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("status", result.status.value)
        table.add_row("turns", str(getattr(result, "turns", "-")))
        table.add_row("retry_safe", str(result.retry_safe).lower())
        table.add_row("effects", str(len(result.effects)))
        table.add_row("records", str(len(result.execution_records)))
        if result.error is not None:
            table.add_row("error", f"{result.error.code}: {result.error.message}")
        self.console.print(table)


class RichApprovalProvider:
    def __init__(
        self, console: Console | None = None, *, assume_yes: bool = False
    ) -> None:
        self.console = console or Console()
        self.assume_yes = assume_yes

    async def request(self, request: ApprovalRequest, cancellation) -> ApprovalResponse:
        preview = json.dumps(
            thaw_json(request.arguments), ensure_ascii=False, separators=(",", ":")
        )
        if len(preview) > 2048:
            preview = preview[:2048] + "…"
        lineage = request.lineage
        lineage_text = "unavailable"
        delegation = "root"
        if lineage is not None:
            lineage_text = (
                f"root={lineage.root_run_id} run={lineage.execution_run_id} "
                f"scope={lineage.scope_id} depth={lineage.agent_depth}"
            )
            delegation = f"agent depth {lineage.agent_depth}"
        body = (
            f"Tool: {request.tool_name}\n"
            f"Effect capability: {request.effect_capability or 'unknown'}\n"
            f"Execution lineage: {lineage_text}\n"
            f"Delegation path: {delegation}\n"
            f"Arguments: {preview}"
        )
        if request.reason:
            body += f"\nReason: {request.reason}"
        if request.effect_capability == "side_effecting":
            body += "\n\nNested/composite execution may request approval again for concrete side effects."
        self.console.print(
            Panel(body, title="Approval required", border_style="yellow")
        )
        if self.assume_yes:
            decision = ApprovalDecision.APPROVE
        else:
            cancellation.raise_if_cancelled()
            answer = await asyncio.to_thread(self.console.input, "Approve? [y/N] ")
            cancellation.raise_if_cancelled()
            decision = (
                ApprovalDecision.APPROVE
                if answer.strip().lower() in {"y", "yes"}
                else ApprovalDecision.REJECT
            )
        return ApprovalResponse(request.approval_id, request.arguments_digest, decision)
