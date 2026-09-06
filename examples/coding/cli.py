"""Rich command-line entry point for the RoboAgent V1.3 coding harness."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from roboagent import Agent
from roboagent.config import load_app_config
from roboagent.context import PromptInput
from roboagent.message import UserMessage
from roboagent.model import ConfiguredModelProvider
from roboagent.runtime import RunStatus
from roboagent.tool import (
    ApplyPatchConfig,
    FilesystemConfig,
    FilesystemWorkspace,
    LocalWorkspace,
    ShellConfig,
    ToolDecision,
    ToolEffectKind,
    ToolPolicyDecision,
    ToolRegistry,
    WorkspaceToolResultMaterializer,
    create_apply_patch_tool,
    create_filesystem_tools,
    create_shell_tool,
)

from .display import CodingDisplay, RichApprovalProvider
from .harness import CodingConfig, CodingSession, create_coding_session

SYSTEM_PROMPT = (
    "You are RoboAgent's coding reference agent. Inspect the repository before editing, "
    "make minimal changes, run relevant tests, and ground completion claims in tool evidence."
)


class CodingApprovalPolicy:
    def __init__(self, *, unsafe_python: bool = False) -> None:
        self.unsafe_python = unsafe_python

    async def evaluate(self, call, tool, context):
        if tool is None:
            return ToolPolicyDecision(ToolDecision.ALLOW)
        if call.name == "execute_python" and not self.unsafe_python:
            return ToolPolicyDecision(ToolDecision.ALLOW)
        if tool.effect_kind is ToolEffectKind.SIDE_EFFECTING:
            return ToolPolicyDecision(
                ToolDecision.REQUIRE_APPROVAL, "side-effecting capability"
            )
        return ToolPolicyDecision(ToolDecision.ALLOW)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m examples.coding",
        description="RoboAgent V1.3 coding reference agent",
    )
    parser.add_argument("task", nargs="?", help="Run one coding task.")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Continue accepting tasks and runtime commands.",
    )
    parser.add_argument(
        "--config", type=Path, help="RoboAgent YAML configuration path."
    )
    parser.add_argument("--model", help="Configured model name.")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root (default: current directory).",
    )
    parser.add_argument(
        "--unsafe-python",
        action="store_true",
        help="Enable trusted, non-sandboxed Python execution.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Approve side-effecting Tool requests without prompting.",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=16,
        help="Provider calls allowed per coding Run.",
    )
    parser.add_argument(
        "--no-shell", action="store_true", help="Do not expose the shell Tool."
    )
    return parser


async def create_cli_session(
    args: argparse.Namespace, console: Console
) -> tuple[CodingSession, ConfiguredModelProvider]:
    workspace_root = args.workspace.expanduser().resolve()
    if not workspace_root.is_dir():
        raise ValueError("Workspace must be an existing directory.")
    app = load_app_config(args.config)
    registry = app.to_model_registry()
    available = registry.list_all(enabled_only=True)
    selected = args.model or registry.default_model
    if selected is None:
        if len(available) != 1:
            raise ValueError(
                "Select --model or configure exactly one enabled/default model."
            )
        selected = available[0].name
    provider = ConfiguredModelProvider(registry)
    try:
        model = provider.get_model(selected)
        filesystem = FilesystemConfig(FilesystemWorkspace(workspace_root))
        tools = list(create_filesystem_tools(filesystem))
        tools.append(create_apply_patch_tool(ApplyPatchConfig(filesystem)))
        if not args.no_shell:
            tools.append(create_shell_tool(ShellConfig(filesystem.workspace)))
        base = Agent(
            model,
            tool_registry=ToolRegistry(tuple(tools)),
            prompt=PromptInput(SYSTEM_PROMPT),
            tool_policy=CodingApprovalPolicy(unsafe_python=args.unsafe_python),
            approval_provider=RichApprovalProvider(console, assume_yes=args.yes),
        )
        runtime_workspace = LocalWorkspace(workspace_root / ".roboagent/runtime")
        coding = create_coding_session(
            base,
            config=CodingConfig(
                max_provider_calls=args.max_provider_calls,
                observation_root=workspace_root / ".roboagent/artifacts",
            ),
            unsafe_python=args.unsafe_python,
            workspace=runtime_workspace,
            result_materializer=WorkspaceToolResultMaterializer(
                workspace=runtime_workspace
            ),
        )
        return coding, provider
    except BaseException:
        await provider.close()
        raise


async def run_cli(args: argparse.Namespace, console: Console | None = None) -> int:
    output = console or Console()
    if args.unsafe_python:
        output.print(
            "[bold yellow]Trusted Python execution is not sandboxed.[/bold yellow]\n"
            "Python code may access host resources outside the RoboAgent workspace."
        )
    coding: CodingSession | None = None
    provider: ConfiguredModelProvider | None = None
    active_result = None
    try:
        coding, provider = await create_cli_session(args, output)
        display = CodingDisplay(output)
        tasks: list[str] = [args.task] if args.task else []
        interactive = args.interactive or not tasks
        while tasks or interactive:
            if tasks:
                task = tasks.pop(0)
            else:
                task = (
                    await asyncio.to_thread(output.input, "\nTask (/quit to exit): ")
                ).strip()
                if not task:
                    continue
                if task in {"/quit", "/exit"}:
                    break
            active_result = await _drive_run(
                coding, task, display, interactive=interactive
            )
            display.result(active_result)
            if not interactive and not tasks:
                break
        if active_result is None:
            return 0
        return _exit_code(active_result.status)
    except asyncio.CancelledError:
        if coding is not None:
            coding.cancel()
        return 2
    finally:
        if coding is not None:
            await asyncio.shield(coding.close())
        if provider is not None:
            await asyncio.shield(provider.close())


async def _drive_run(
    coding: CodingSession, task: str, display: CodingDisplay, *, interactive: bool
):
    run = coding.start(task)
    subscription = run.subscribe()
    display_task = asyncio.create_task(display.consume(subscription, coding.session))
    result_task = asyncio.create_task(coding.wait(run))
    try:
        if not interactive:
            return await asyncio.shield(result_task)
        while not result_task.done():
            control = asyncio.create_task(
                _read_line(
                    "[running] /steer TEXT, /follow-up TEXT, /cancel, or Enter: "
                )
            )
            done, _ = await asyncio.wait(
                {result_task, control}, return_when=asyncio.FIRST_COMPLETED
            )
            if result_task in done:
                control.cancel()
                await asyncio.gather(control, return_exceptions=True)
                return result_task.result()
            line = control.result().strip()
            if line.startswith("/steer "):
                await coding.steer(line.removeprefix("/steer "))
            elif line.startswith("/follow-up "):
                await coding.session.follow_up(
                    UserMessage(line.removeprefix("/follow-up "))
                )
            elif line == "/cancel":
                coding.cancel()
            elif line in {"/quit", "/exit"}:
                coding.cancel()
        return result_task.result()
    except asyncio.CancelledError:
        coding.cancel()
        await asyncio.shield(result_task)
        raise
    finally:
        await asyncio.gather(display_task, return_exceptions=True)
        subscription.close()


async def _read_line(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "add_reader") or not sys.stdin.isatty():
        return await asyncio.to_thread(input, prompt)
    future: asyncio.Future[str] = loop.create_future()
    sys.stdout.write(prompt)
    sys.stdout.flush()

    def ready() -> None:
        if not future.done():
            future.set_result(sys.stdin.readline())

    loop.add_reader(sys.stdin.fileno(), ready)
    try:
        return await future
    finally:
        loop.remove_reader(sys.stdin.fileno())


def _exit_code(status: RunStatus) -> int:
    if status is RunStatus.COMPLETED:
        return 0
    if status is RunStatus.CANCELLED:
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_cli(args))
    except KeyboardInterrupt:
        return 2
    except Exception as exc:
        Console(stderr=True).print(f"[red]Startup/config error:[/red] {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
