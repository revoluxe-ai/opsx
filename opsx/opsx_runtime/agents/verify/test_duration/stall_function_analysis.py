"""Read-only разбор product-кадра стека pytest stall: неограниченное ожидание / lock / цикл."""

from __future__ import annotations

from opsx.project_context import resource_path

import ast
import re
from pathlib import Path
from typing import Any, Literal

from workflows.verify.gate_repair.static_scope import (
    gate_issue_in_product_static_scope,
    normalize_repo_relative_path,
)

_STACK_FRAME_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\w+)',
)

UnboundedKind = Literal["while_true", "blocking_lock", "unbounded_wait"]


def repo_relative_stack_path(path: str, repo_root: Path) -> str:
    """Путь из стека → относительный к корню репозитория."""
    raw = path.replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            pass
    return normalize_repo_relative_path(raw)


def stack_path_in_product_static_scope(rel_path: str) -> bool:
    """Product static scope (как pyright/mypy gate), не тесты и не ``opsx/``."""
    norm = normalize_repo_relative_path(rel_path)
    return gate_issue_in_product_static_scope(f"{norm}:1: stall")


def _parse_stack_frames(stderr: str) -> list[tuple[str, int, str]]:
    return [
        (path, int(line), func)
        for path, line, func in _STACK_FRAME_RE.findall(stderr)
    ]


def _deepest_product_frame(
    stderr: str,
    repo_root: Path,
) -> tuple[str, int, str] | None:
    """Последний (текущий) кадр стека в product static scope."""
    frames = _parse_stack_frames(stderr)
    for path, line, func in reversed(frames):
        rel = repo_relative_stack_path(path, repo_root)
        if stack_path_in_product_static_scope(rel):
            return rel, line, func
    return None


def _truthy_loop_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value is True:
        return True
    if isinstance(node, ast.Constant) and node.value == 1:
        return True
    if isinstance(node, ast.Name) and node.id == "True":
        return True
    return False


def _call_has_timeout_kw(call: ast.Call) -> bool:
    return any(kw.arg in ("timeout", "time") for kw in call.keywords if kw.arg)


class _UnboundedPatternVisitor(ast.NodeVisitor):
    """Ищет в теле функции явные неограниченные wait/lock/цикл."""

    def __init__(self) -> None:
        self.kind: UnboundedKind | None = None
        self.detail: str = ""

    def visit_While(self, node: ast.While) -> None:
        if self.kind is not None:
            return
        if _truthy_loop_test(node.test):
            self.kind = "while_true"
            self.detail = f"бесконечный while на строке {node.lineno}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.kind is not None:
            return
        attr: str | None = None
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
        if attr == "acquire" and not _call_has_timeout_kw(node):
            if not node.args or (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value is True
            ):
                self.kind = "blocking_lock"
                self.detail = f"блокирующий acquire без timeout на строке {node.lineno}"
        elif attr == "wait" and not _call_has_timeout_kw(node) and not node.args:
            self.kind = "unbounded_wait"
            self.detail = f"wait() без timeout на строке {node.lineno}"
        elif attr == "join" and not _call_has_timeout_kw(node) and len(node.args) <= 1:
            self.kind = "unbounded_wait"
            self.detail = f"join() без timeout на строке {node.lineno}"
        self.generic_visit(node)


def _host_function_at_line(
    tree: ast.AST,
    line: int,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= line <= end:
            if best is None or node.lineno >= best.lineno:
                best = node
    return best


def analyze_product_frame_function(
    repo_root: Path,
    rel_path: str,
    line: int,
    function_name: str,
) -> dict[str, Any]:
    """Read-only AST-проверка: функция на ``line`` содержит неограниченное ожидание.

    Returns:
        ``stall_function_analysis`` — поле для JSON stall evidence; ``unbounded_wait_proven``
        true только при явном while True / blocking lock / wait без timeout.
    """
    source_path = resource_path(repo_root, rel_path.replace("/", "\\"))
    base: dict[str, Any] = {
        "product_frame": {
            "path": rel_path,
            "line": line,
            "function": function_name,
        },
        "unbounded_wait_proven": False,
        "kind": None,
        "detail": "",
    }
    if not source_path.is_file():
        base["detail"] = "исходный файл product-кадра не найден"
        return base
    try:
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel_path)
    except (OSError, SyntaxError, UnicodeError) as exc:
        base["detail"] = f"не удалось разобрать исходник: {exc}"
        return base
    host = _host_function_at_line(tree, line)
    if host is None:
        base["detail"] = "не найдена host-функция для строки стека"
        return base
    if host.name != function_name:
        base["detail"] = (
            f"имя в стеке ({function_name}) не совпало с AST ({host.name})"
        )
    visitor = _UnboundedPatternVisitor()
    for child in ast.iter_child_nodes(host):
        visitor.visit(child)
    if visitor.kind:
        base["unbounded_wait_proven"] = True
        base["kind"] = visitor.kind
        base["detail"] = visitor.detail
    else:
        base["detail"] = "в теле функции нет явного неограниченного wait/lock/цикла"
    return base


def stall_function_analysis_from_stderr(
    stderr: str,
    repo_root: Path,
) -> dict[str, Any] | None:
    """Собрать ``stall_function_analysis`` по хвосту stderr со стеком (или ``None``)."""
    frame = _deepest_product_frame(stderr, repo_root)
    if frame is None:
        return None
    rel, line, func = frame
    return analyze_product_frame_function(repo_root, rel, line, func)
