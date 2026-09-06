from pathlib import Path


def test_smolagents_attribution_and_dependency_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    notice = (root / "examples/coding/NOTICE.md").read_text()
    audit = (root / "examples/coding/UPSTREAM_AUDIT.md").read_text()
    project = (root / "pyproject.toml").read_text()
    evaluator = (root / "examples/coding/evaluator.py").read_text()
    commit = "30bb1161095dbae2271e6bc3cc4c219cc3897a57"
    assert commit in notice and commit in audit and commit in evaluator
    assert "Apache License 2.0" in notice
    assert "src/smolagents/local_python_executor.py" in notice
    assert "coding = [" in project and '"rich>=13"' in project
    dependency_block = project.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "smolagents" not in dependency_block
