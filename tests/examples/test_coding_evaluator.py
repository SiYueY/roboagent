from __future__ import annotations

from examples.coding.evaluator import AstEvaluator


def test_persistent_state_import_and_user_error() -> None:
    evaluator = AstEvaluator(lambda name, args, kwargs: None)
    assert evaluator.execute("x = 40\nprint(x + 2)", set()).stdout == "42\n"
    assert (
        evaluator.execute("import math\nprint(math.sqrt(x))", set()).stdout
        == "6.324555320336759\n"
    )
    failed = evaluator.execute("1 / 0", set())
    assert failed.error == "ZeroDivisionError: division by zero"
    assert not failed.is_final


def test_import_and_reflection_boundary() -> None:
    evaluator = AstEvaluator(lambda name, args, kwargs: None)
    assert "not authorized" in (evaluator.execute("import os", set()).error or "")
    assert "Dunder reflection" in (
        evaluator.execute("(1).__class__", set()).error or ""
    )
    assert "NameError: open" == evaluator.execute("open('x')", set()).error


def test_final_answer_unwinds_operands_and_except_but_runs_finally() -> None:
    evaluator = AstEvaluator(lambda name, args, kwargs: None)
    result = evaluator.execute(
        "try:\n"
        "    try:\n"
        "        final_answer('done') + print('never')\n"
        "    except BaseException:\n"
        "        print('caught')\n"
        "finally:\n"
        "    print('cleanup')\n"
        "print('later')\n",
        set(),
    )
    assert result.is_final
    assert result.final == {"kind": "text", "value": "done"}
    assert result.stdout == "cleanup\n"


def test_tool_proxy_errors_can_be_caught() -> None:
    calls = []
    evaluator = AstEvaluator(
        lambda name, args, kwargs: calls.append((name, args, kwargs)) or "ok"
    )
    result = evaluator.execute("print(read_file('a'))", {"read_file"})
    assert result.stdout == "ok\n"
    assert calls == [("read_file", ("a",), {})]


def test_last_expression_and_assignment_are_observable_without_print() -> None:
    evaluator = AstEvaluator(lambda name, args, kwargs: {"items": ["README.md"]})
    expression = evaluator.execute("list_files()", {"list_files"})
    assert expression.stdout == (
        "Last output from code snippet:\n{'items': ['README.md']}\n"
    )
    assignment = evaluator.execute("files = list_files()", {"list_files"})
    assert assignment.stdout == (
        "Last output from code snippet:\n{'items': ['README.md']}\n"
    )


def test_trusted_mode_adds_host_imports_without_bare_exec() -> None:
    trusted = AstEvaluator(lambda name, args, kwargs: None, trusted=True)
    assert (
        trusted.execute("import os\nprint(bool(os.getcwd()))", set()).stdout == "True\n"
    )
    assert trusted.execute("exec('x = 1')", set()).error == "NameError: exec"
