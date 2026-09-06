from __future__ import annotations

import asyncio
import json
from io import StringIO

from rich.console import Console

from examples.coding.cli import _exit_code, build_parser
from examples.coding.display import CodingDisplay, RichApprovalProvider
from roboagent.message import (
    FrozenJsonObject,
    JsonContent,
    ToolResultMessage,
    ToolResultStatus,
    canonical_json_digest,
)
from roboagent.runtime import (
    AgentEvent,
    ExecutionLineage,
    RunStatus,
    RuntimeCancellation,
)
from roboagent.tool import ApprovalDecision, ApprovalRequest


def test_cli_parser_and_exit_codes() -> None:
    parser = build_parser()
    args = parser.parse_args(["--workspace", ".", "fix it"])
    assert args.task == "fix it"
    assert not args.unsafe_python
    assert _exit_code(RunStatus.COMPLETED) == 0
    assert _exit_code(RunStatus.FAILED) == 1
    assert _exit_code(RunStatus.CANCELLED) == 2


def test_display_renders_streamed_python_code() -> None:
    target = StringIO()
    display = CodingDisplay(Console(file=target, force_terminal=False, width=100))
    call_id = "call"
    display.event(
        AgentEvent(
            "run",
            0,
            "model.tool_call_started",
            FrozenJsonObject(
                {
                    "tool_call_id": call_id,
                    "tool_name": "execute_python",
                }
            ),
        ),
        None,
    )
    display.event(
        AgentEvent(
            "run",
            1,
            "model.tool_call_arguments_delta",
            FrozenJsonObject(
                {
                    "tool_call_id": call_id,
                    "delta": json.dumps({"code": "print(42)"}),
                }
            ),
        ),
        None,
    )
    display.event(
        AgentEvent(
            "run",
            2,
            "model.tool_call_completed",
            FrozenJsonObject(
                {
                    "tool_call_id": call_id,
                    "tool_name": "execute_python",
                }
            ),
        ),
        None,
    )
    assert "Python action" in target.getvalue()
    assert "print(42)" in target.getvalue()


def test_display_preserves_rich_markup_literals_and_skips_empty_final_observation() -> (
    None
):
    target = StringIO()
    display = CodingDisplay(Console(file=target, force_terminal=False, width=100))
    observation = ToolResultMessage(
        "call",
        "execute_python",
        ToolResultStatus.SUCCESS,
        (JsonContent({"observation": "[project]", "is_final": False}),),
    )
    display.render_latest_observations((observation,))
    rendered = target.getvalue()
    assert "Python observation" in rendered
    assert "[project]" in rendered

    final = ToolResultMessage(
        "final",
        "execute_python",
        ToolResultStatus.SUCCESS,
        (JsonContent({"observation": "", "is_final": True}),),
    )
    display.render_latest_observations((final,))
    assert target.getvalue() == rendered


def test_rich_approval_shows_bounded_arguments_lineage_and_effect() -> None:
    async def check() -> None:
        target = StringIO()
        console = Console(file=target, force_terminal=False, width=120)
        arguments = FrozenJsonObject({"path": "module.py"})
        lineage = ExecutionLineage(
            "root", "child", "scope", "parent", 2, 1, "call", "delegate"
        )
        request = ApprovalRequest(
            "approval",
            "child",
            "session",
            "call",
            "apply_patch",
            arguments,
            canonical_json_digest(arguments),
            "write",
            lineage,
            "side_effecting",
        )
        response = await RichApprovalProvider(console, assume_yes=True).request(
            request, RuntimeCancellation()
        )
        rendered = target.getvalue()
        assert response.decision is ApprovalDecision.APPROVE
        assert "module.py" in rendered
        assert "scope=scope" in rendered
        assert "side_effecting" in rendered
        assert "agent depth 1" in rendered
        assert "may request approval again" in rendered

    asyncio.run(check())
