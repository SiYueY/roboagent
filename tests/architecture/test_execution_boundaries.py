from __future__ import annotations

import ast
from pathlib import Path


PACKAGE = Path(__file__).parents[2] / "roboagent"
OUTER_LAYERS = (PACKAGE / "agent", PACKAGE / "tool")


def test_agent_and_tool_do_not_reach_through_execution_private_state() -> None:
    violations: list[str] = []
    for root in OUTER_LAYERS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {"_scope", "_tree"}:
                    violations.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert violations == [], (
        "Agent/Tool layers must use ExecutionContext semantic APIs: "
        + ", ".join(violations)
    )


def test_agent_and_tool_do_not_import_execution_tree_or_scope() -> None:
    forbidden = {"ExecutionTree", "ExecutionScope"}
    violations: list[str] = []
    for root in OUTER_LAYERS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module not in {"roboagent.runtime", "roboagent.runtime.execution"}:
                    continue
                names = forbidden.intersection(alias.name for alias in node.names)
                if names:
                    violations.append(
                        f"{path.relative_to(PACKAGE)}:{node.lineno}:{','.join(sorted(names))}"
                    )
    assert violations == [], (
        "ExecutionTree/ExecutionScope are runtime internals: "
        + ", ".join(violations)
    )
