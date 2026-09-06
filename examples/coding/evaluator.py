"""Persistent AST evaluator adapted from smolagents' LocalPythonExecutor design.

The node evaluator and state model are based on
``smolagents/local_python_executor.py`` at commit
30bb1161095dbae2271e6bc3cc4c219cc3897a57 (Apache-2.0).  It is deliberately
independent of smolagents' Agent, Model, Memory, and Tool runtimes.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import io
import operator
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable, cast

from .protocol import (
    ArtifactHandle,
    CodingProtocolError,
    RoboAgentToolError,
    final_value,
)

AUTHORIZED_IMPORTS = frozenset(
    {
        "math",
        "statistics",
        "json",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
    }
)
_NEVER_CALLABLE_BUILTINS = frozenset({"exec", "eval", "compile", "__import__"})
_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
    )
}
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.MatMult: operator.matmul,
}
_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


class _FinalSignal(BaseException):
    def __init__(self, value: object) -> None:
        self.value = final_value(value)


class _Break(BaseException):
    pass


class _Continue(BaseException):
    pass


class _Return(BaseException):
    def __init__(self, value: object) -> None:
        self.value = value


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    stdout: str
    is_final: bool
    final: dict[str, object] | None
    error: str | None = None


class AstEvaluator:
    """A capability-oriented evaluator with state scoped to one worker generation."""

    def __init__(
        self,
        tool_call: Callable[[str, tuple[object, ...], dict[str, object]], object],
        *,
        trusted: bool = False,
    ) -> None:
        self.state: dict[str, object] = {}
        self.tool_call = tool_call
        self.trusted = trusted
        self.tool_aliases: set[str] = set()
        self._completion: dict[str, object] | None = None

    def execute(self, code: str, tool_aliases: set[str]) -> EvaluationResult:
        self.tool_aliases = set(tool_aliases)
        self._completion = None
        output = io.StringIO()
        try:
            tree = ast.parse(code, mode="exec")
            with contextlib.redirect_stdout(output):
                result = self._block(tree.body, self.state)
                if result is not None:
                    print("Last output from code snippet:")
                    print(repr(result))
        except _FinalSignal as signal:
            return EvaluationResult(
                output.getvalue(), True, self._completion or signal.value
            )
        except CodingProtocolError:
            raise
        except BaseException as exc:
            if self._completion is not None:
                return EvaluationResult(output.getvalue(), True, self._completion)
            return EvaluationResult(
                output.getvalue(), False, None, f"{type(exc).__name__}: {exc}"
            )
        return EvaluationResult(output.getvalue(), False, None)

    def _block(self, statements: list[ast.stmt], scope: dict[str, object]) -> object:
        result: object = None
        for statement in statements:
            result = self._stmt(statement, scope)
        return result

    def _stmt(self, node: ast.stmt, scope: dict[str, object]) -> object:
        if isinstance(node, ast.Expr):
            return self._expr(node.value, scope)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(node, ast.Assign):
                value = self._expr(node.value, scope)
                for target in node.targets:
                    self._assign(target, value, scope)
            elif isinstance(node, ast.AnnAssign):
                if node.value is not None:
                    value = self._expr(node.value, scope)
                    self._assign(node.target, value, scope)
                else:
                    value = None
            else:
                current = self._expr(node.target, scope)
                value = _BINOPS[type(node.op)](current, self._expr(node.value, scope))
                self._assign(node.target, value, scope)
            return value
        if isinstance(node, ast.If):
            return self._block(
                node.body if self._expr(node.test, scope) else node.orelse, scope
            )
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node, ast.AsyncFor):
                raise TypeError("Async execution is unsupported")
            broke = False
            for value in cast(Iterable[object], self._expr(node.iter, scope)):
                self._assign(node.target, value, scope)
                try:
                    self._block(node.body, scope)
                except _Continue:
                    continue
                except _Break:
                    broke = True
                    break
            if not broke:
                self._block(node.orelse, scope)
            return None
        if isinstance(node, ast.While):
            broke = False
            while self._expr(node.test, scope):
                try:
                    self._block(node.body, scope)
                except _Continue:
                    continue
                except _Break:
                    broke = True
                    break
            if not broke:
                self._block(node.orelse, scope)
            return None
        if isinstance(node, ast.Break):
            raise _Break()
        if isinstance(node, ast.Continue):
            raise _Continue()
        if isinstance(node, ast.Return):
            raise _Return(None if node.value is None else self._expr(node.value, scope))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.AsyncFunctionDef):
                raise TypeError("Async functions are unsupported")
            scope[node.name] = self._function(node, scope)
            return None
        if isinstance(node, ast.Try):
            try:
                try:
                    self._block(node.body, scope)
                except (_FinalSignal, _Return, _Break, _Continue):
                    raise
                except BaseException as exc:
                    handled = False
                    for handler in node.handlers:
                        if handler.type is None or self._exception_matches(
                            exc, handler.type, scope
                        ):
                            if handler.name:
                                scope[handler.name] = exc
                            self._block(handler.body, scope)
                            handled = True
                            break
                    if not handled:
                        raise
                else:
                    self._block(node.orelse, scope)
            finally:
                self._block(node.finalbody, scope)
            return None
        if isinstance(node, ast.Raise):
            if node.exc is None:
                raise RuntimeError("Bare raise is unsupported")
            value = self._expr(node.exc, scope)
            if not isinstance(value, BaseException):
                raise TypeError("exceptions must derive from BaseException")
            raise value
        if isinstance(node, ast.Assert):
            if not self._expr(node.test, scope):
                raise AssertionError(
                    None if node.msg is None else self._expr(node.msg, scope)
                )
            return None
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if not self.trusted and root not in AUTHORIZED_IMPORTS:
                    raise ImportError(f"Import of {root!r} is not authorized")
                scope[alias.asname or root] = importlib.import_module(alias.name)
            return None
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                not self.trusted and module.split(".")[0] not in AUTHORIZED_IMPORTS
            ) or node.level:
                raise ImportError(f"Import of {module!r} is not authorized")
            imported = importlib.import_module(module)
            for alias in node.names:
                if alias.name == "*":
                    raise ImportError("Star imports are unsupported")
                scope[alias.asname or alias.name] = getattr(imported, alias.name)
            return None
        if isinstance(node, ast.Pass):
            return None
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    raise TypeError("Only name deletion is supported")
                del scope[target.id]
            return None
        raise TypeError(f"Unsupported statement: {type(node).__name__}")

    def _expr(self, node: ast.expr, scope: dict[str, object]) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "final_answer":
                return self._finish
            if node.id in scope:
                return scope[node.id]
            if node.id == "ArtifactHandle":
                return ArtifactHandle
            if node.id == "RoboAgentToolError":
                return RoboAgentToolError
            if node.id in _SAFE_BUILTINS:
                return _SAFE_BUILTINS[node.id]
            if (
                self.trusted
                and node.id not in _NEVER_CALLABLE_BUILTINS
                and hasattr(builtins, node.id)
            ):
                return getattr(builtins, node.id)
            if node.id in {
                "Exception",
                "BaseException",
                "ValueError",
                "TypeError",
                "RuntimeError",
            }:
                return getattr(builtins, node.id)
            if node.id in self.tool_aliases:
                return lambda *args, _name=node.id, **kwargs: self.tool_call(
                    _name, args, kwargs
                )
            raise NameError(node.id)
        if isinstance(node, ast.List):
            return [self._expr(item, scope) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._expr(item, scope) for item in node.elts)
        if isinstance(node, ast.Set):
            return {self._expr(item, scope) for item in node.elts}
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise TypeError("Dictionary unpacking is unsupported")
            return {
                self._expr(cast(ast.expr, key), scope): self._expr(value, scope)
                for key, value in zip(node.keys, node.values, strict=True)
            }
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](
                self._expr(node.left, scope), self._expr(node.right, scope)
            )
        if isinstance(node, ast.UnaryOp):
            value = self._expr(node.operand, scope)
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value  # type: ignore[operator]
            if isinstance(node.op, ast.UAdd):
                return +value  # type: ignore[operator]
            return ~value  # type: ignore[operator]
        if isinstance(node, ast.BoolOp):
            value = self._expr(node.values[0], scope)
            for part in node.values[1:]:
                if (
                    isinstance(node.op, ast.And)
                    and not value
                    or isinstance(node.op, ast.Or)
                    and value
                ):
                    return value
                value = self._expr(part, scope)
            return value
        if isinstance(node, ast.Compare):
            left = self._expr(node.left, scope)
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._expr(comparator, scope)
                if not _CMPOPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._expr(
                node.body if self._expr(node.test, scope) else node.orelse, scope
            )
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise AttributeError("Dunder reflection is forbidden")
            return getattr(self._expr(node.value, scope), node.attr)
        if isinstance(node, ast.Subscript):
            return self._expr(node.value, scope)[self._expr(node.slice, scope)]  # type: ignore[index]
        if isinstance(node, ast.Slice):
            return slice(
                None if node.lower is None else self._expr(node.lower, scope),
                None if node.upper is None else self._expr(node.upper, scope),
                None if node.step is None else self._expr(node.step, scope),
            )
        if isinstance(node, ast.Call):
            function = self._expr(node.func, scope)
            args = [self._expr(item, scope) for item in node.args]
            kwargs = {
                item.arg: self._expr(item.value, scope)
                for item in node.keywords
                if item.arg is not None
            }
            return cast(Callable[..., object], function)(*args, **kwargs)
        if isinstance(node, ast.JoinedStr):
            return "".join(
                str(self._expr(item.value, scope))
                if isinstance(item, ast.FormattedValue)
                else str(cast(ast.Constant, item).value)
                for item in node.values
            )
        if isinstance(node, ast.ListComp):
            return list(self._comprehension(node.elt, node.generators, scope))
        if isinstance(node, ast.SetComp):
            return set(self._comprehension(node.elt, node.generators, scope))
        if isinstance(node, ast.DictComp):
            return {key: value for key, value in self._dict_comprehension(node, scope)}
        if isinstance(node, ast.Lambda):
            return self._lambda(node, scope)
        raise TypeError(f"Unsupported expression: {type(node).__name__}")

    def _finish(self, value: object = None) -> None:
        if self._completion is None:
            self._completion = final_value(value)
        raise _FinalSignal(self._completion)

    def _assign(
        self, target: ast.expr, value: object, scope: dict[str, object]
    ) -> None:
        if isinstance(target, ast.Name):
            scope[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = list(cast(Iterable[object], value))
            if len(values) != len(target.elts):
                raise ValueError("unpack mismatch")
            for child, item in zip(target.elts, values, strict=True):
                self._assign(child, item, scope)
        elif isinstance(target, ast.Subscript):
            self._expr(target.value, scope)[self._expr(target.slice, scope)] = value  # type: ignore[index]
        elif isinstance(target, ast.Attribute):
            if target.attr.startswith("__"):
                raise AttributeError("Dunder reflection is forbidden")
            setattr(self._expr(target.value, scope), target.attr, value)
        else:
            raise TypeError("Unsupported assignment target")

    def _function(self, node: ast.FunctionDef, closure: dict[str, object]):
        names = [item.arg for item in node.args.args]
        defaults = [self._expr(item, closure) for item in node.args.defaults]

        def function(*args, **kwargs):
            local = dict(closure)
            required = len(names) - len(defaults)
            for name, value in zip(names, args, strict=False):
                local[name] = value
            for name, value in zip(
                names[-len(defaults) :] if defaults else (), defaults, strict=True
            ):
                local.setdefault(name, value)
            local.update(kwargs)
            if any(name not in local for name in names[:required]):
                raise TypeError("missing required arguments")
            try:
                self._block(node.body, local)
            except _Return as returned:
                return returned.value
            return None

        return function

    def _lambda(self, node: ast.Lambda, closure: dict[str, object]):
        names = [item.arg for item in node.args.args]
        return lambda *args: self._expr(
            node.body, {**closure, **dict(zip(names, args, strict=False))}
        )

    def _comprehension(
        self,
        expression: ast.expr,
        generators: list[ast.comprehension],
        scope: dict[str, object],
    ):
        def walk(index: int):
            if index == len(generators):
                yield self._expr(expression, scope)
                return
            generator = generators[index]
            if generator.is_async:
                raise TypeError("Async comprehensions are unsupported")
            for item in cast(Iterable[object], self._expr(generator.iter, scope)):
                self._assign(generator.target, item, scope)
                if all(self._expr(condition, scope) for condition in generator.ifs):
                    yield from walk(index + 1)

        return walk(0)

    def _dict_comprehension(self, node: ast.DictComp, scope: dict[str, object]):
        marker = ast.Tuple(elts=[node.key, node.value], ctx=ast.Load())
        return self._comprehension(marker, node.generators, scope)

    def _exception_matches(
        self, exc: BaseException, node: ast.expr, scope: dict[str, object]
    ) -> bool:
        expected = self._expr(node, scope)
        return isinstance(exc, expected)  # type: ignore[arg-type]
