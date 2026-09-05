from __future__ import annotations

import asyncio
from pathlib import Path

from roboagent.message import FrozenJsonObject
from roboagent.runtime import EventSubscriptionConfig, RunEventEmitter, RuntimeCancellation
from roboagent.skill import SkillManager, SkillSource, create_read_skill_tool
from roboagent.tool import ToolContext, ToolExecutionMode, ToolTextContent


def _skill(root: Path, directory: str, name: str, description: str, body: str) -> None:
    target = root / ".roboagent" / "skills" / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


def test_skill_override_reload_and_run_revision(tmp_path: Path) -> None:
    project = tmp_path / "project"
    user = tmp_path / "user"
    _skill(user, "a", "guide", "User guide", "user body")
    _skill(project, "z", "guide", "Project guide", "project body")
    manager = SkillManager(project_root=project, user_root=user)
    assert manager.catalog.metadata[0].source is SkillSource.PROJECT
    assert any(item.code == "skill_overridden" for item in manager.diagnostics)
    old = manager.bind_run("run-old")
    assert old.load("guide") == "project body"
    _skill(project, "z", "guide", "Changed", "new body")
    manager.reload()
    assert manager.load("guide", run_id="run-old") == "project body"
    assert manager.load("guide") == "new body"

    async def check() -> None:
        tool = create_read_skill_tool(manager)
        assert tool.execution_mode is ToolExecutionMode.CONCURRENT
        result = await tool.execute(FrozenJsonObject({"name": "guide"}), ToolContext("run-old", "session", RuntimeCancellation()))
        assert isinstance(result, ToolTextContent) and result.text == "project body"

    asyncio.run(check())


def test_skill_invalid_and_duplicate_diagnostics_are_deterministic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    user = tmp_path / "user"
    _skill(project, "b", "same", "B", "b")
    _skill(project, "a", "same", "A", "a")
    _skill(project, "bad", "Bad_Name", "bad", "bad")
    manager = SkillManager(project_root=project, user_root=user)
    assert manager.catalog.entries == ()
    duplicate = next(item for item in manager.diagnostics if item.code == "duplicate_skill_name")
    assert [path.parent.name for path in duplicate.paths] == ["a", "b"]
    assert any(item.code == "invalid_skill" for item in manager.diagnostics)


def test_event_replay_overflow_and_terminal_are_bounded() -> None:
    async def check() -> None:
        emitter = RunEventEmitter("run", EventSubscriptionConfig(max_queue_size=2, replay_limit=2))
        subscription = emitter.subscribe()
        await emitter.emit("run.started")
        await emitter.emit("model.started")
        await emitter.emit("model.delta", text="x")
        await emitter.emit("run.completed")
        events = [event async for event in subscription]
        assert len(events) <= 2
        assert events[-1].type == "run.completed"
        assert emitter.dropped_events > 0
        late = [event async for event in emitter.subscribe()]
        assert late[-1].type == "run.completed"
        assert [event.sequence for event in late] == sorted(event.sequence for event in late)

    asyncio.run(check())
