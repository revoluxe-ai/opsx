"""Признак «сценарный тест не исполнять в OPSX verify workflow».

Repair-hop не поднимает pytest: defer и маркеры решаются до независимого verify.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import re
from pathlib import Path

from agents.common.scenario_files import scenario_test_relpath
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings
from agents.common.pytest_requires_postgres_path import is_requires_postgres_path

WORKFLOW_DEFERRED_MARKER = "workflow_deferred"
_MARKER_SNIPPET = f"pytest.mark.{WORKFLOW_DEFERRED_MARKER}"
_FUTURE_IMPORT = re.compile(r"^\s*from\s+__future__\s+import\s+")
_ENCODING_COOKIE = re.compile(r"coding[:=]\s*[-\w.]+")


def _node_parts(node_id: str) -> tuple[str, str | None]:
    """Разобрать ``tests/.../test_x.py::test_y`` на путь и имя функции."""
    if "::" not in node_id:
        return node_id, None
    path, func = node_id.split("::", 1)
    return path, func.split("[", 1)[0]


def _ast_has_workflow_deferred_decorator(tree: ast.Module, func_name: str) -> bool:
    """Проверить ``@pytest.mark.workflow_deferred`` на конкретной test-функции."""
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for deco in node.decorator_list:
            target = deco
            if isinstance(deco, ast.Call):
                target = deco.func
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
                if (
                    target.attr == WORKFLOW_DEFERRED_MARKER
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "pytest"
                    and target.value.attr == "mark"
                ):
                    return True
    return False


def _source_declares_workflow_deferred(source: str, func_name: str | None) -> bool:
    """По тексту модуля: явный ``@pytest.mark.workflow_deferred`` на тесте."""
    if _MARKER_SNIPPET not in source:
        return False
    if re.search(rf"pytestmark\s*=\s*\[.*{WORKFLOW_DEFERRED_MARKER}", source, re.DOTALL):
        return True
    if func_name is None:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return _ast_has_workflow_deferred_decorator(tree, func_name)


def _discover_mapped_node(repo: Path, change_id: str, scenario_id: str) -> str | None:
    """Найти pytest node id mapped-теста сценария (matrix или canonical file)."""
    node = str(trace_mappings(repo, change_id).get(scenario_id) or "")
    if "::" in node:
        return node
    rel = scenario_test_relpath(change_id, scenario_id)
    path = (resource_path(repo, rel)).resolve()
    if not path.is_file():
        return None
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError):
        return None
    lines = source.splitlines(keepends=True)
    id_line = f"SCENARIO_ID: {scenario_id}"
    for item in tree.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not item.name.startswith("test_"):
            continue
        end = item.end_lineno or item.lineno
        body = "".join(lines[item.lineno - 1 : end])
        if scenario_id in body and id_line in body:
            return f"{rel}::{item.name}"
    return None


def _skip_insignificant(lines: list[str], idx: int) -> int:
    """Пропустить пустые строки и комментарии; это не statements."""
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped and not stripped.startswith("#"):
            return idx
        idx += 1
    return idx


def _module_header_end(lines: list[str]) -> int:
    """Индекс первой строки после shebang, cookie, docstring и ``__future__``.

    Пустые строки и комментарии между docstring и future-import не сдвигают
    точку вставки перед ``from __future__``.
    """
    idx = 0
    if lines and lines[0].startswith("#!"):
        idx = 1
    if idx < len(lines) and _ENCODING_COOKIE.search(lines[idx]):
        idx += 1
    idx = _skip_insignificant(lines, idx)
    if idx < len(lines) and lines[idx].lstrip().startswith(('"""', "'''")):
        quote = '"""' if '"""' in lines[idx] else "'''"
        if lines[idx].count(quote) >= 2 and lines[idx].strip().endswith(quote):
            idx += 1
        else:
            scan = idx + 1
            while scan < len(lines):
                if quote in lines[scan]:
                    idx = scan + 1
                    break
                scan += 1
    idx = _skip_insignificant(lines, idx)
    while idx < len(lines) and _FUTURE_IMPORT.match(lines[idx]):
        idx += 1
        idx = _skip_insignificant(lines, idx)
    return idx


def _ensure_pytest_import(source: str) -> str:
    """Добавить ``import pytest`` после header/future-imports, не ломая compile()."""
    if re.search(r"^\s*import pytest\b", source, re.MULTILINE):
        return source
    if re.search(r"^\s*from pytest import", source, re.MULTILINE):
        return source
    lines = source.splitlines(keepends=True)
    insert_at = _module_header_end(lines)
    lines.insert(insert_at, "import pytest\n")
    if insert_at < len(lines) - 1 and lines[insert_at + 1].strip():
        lines.insert(insert_at + 1, "\n")
    merged = "".join(lines)
    try:
        _ = compile(merged, "<workflow_pytest_defer>", "exec")
    except SyntaxError:
        return source
    return merged


def _inject_workflow_deferred(source: str, func_name: str) -> str:
    """Вставить ``@pytest.mark.workflow_deferred`` непосредственно перед целевым тестом."""
    if _source_declares_workflow_deferred(source, func_name):
        return source
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target = node
            break
    if target is None:
        return source
    lines = source.splitlines(keepends=True)
    insert_at = target.lineno - 1
    if target.decorator_list:
        insert_at = target.decorator_list[0].lineno - 1
    indent_match = re.match(r"^(\s*)", lines[insert_at] if insert_at < len(lines) else "")
    indent = indent_match.group(1) if indent_match else ""
    lines.insert(insert_at, f"{indent}@pytest.mark.{WORKFLOW_DEFERRED_MARKER}\n")
    merged = "".join(lines)
    return _ensure_pytest_import(merged)


def ensure_workflow_deferred_on_node(repo: Path, node_id: str) -> bool:
    """Закрепить маркер в mapped test при author/repair (детерминированная запись).

    Returns:
        True, если файл теста был изменён.
    """
    _rel_path, func_name = _node_parts(node_id)
    if not func_name:
        return False
    path = (resource_path(repo, _rel_path)).resolve()
    if not path.is_file():
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    try:
        updated = _inject_workflow_deferred(source, func_name)
    except SyntaxError:
        return False
    if updated == source:
        return False
    try:
        _ = compile(updated, str(path), "exec")
    except SyntaxError:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def ensure_workflow_deferred_for_scenario(repo: Path, change_id: str, scenario_id: str) -> bool:
    """Поставить маркер на mapped node сценария, если его ещё нет."""
    node = _discover_mapped_node(repo, change_id, scenario_id)
    if not node:
        return False
    return ensure_workflow_deferred_on_node(repo, node)


def validate_mapped_workflow_deferred_marker(repo: Path, node_id: str) -> str | None:
    """Проверка приёмки: tier ``requires_postgres`` обязан иметь явный маркер."""
    rel_path, func_name = _node_parts(node_id)
    test_path = (resource_path(repo, rel_path)).resolve()
    if not test_path.is_file():
        return f"WORKFLOW_DEFERRED_MARKER_MISSING: no file for {node_id}"
    if not is_requires_postgres_path(test_path, tests_root=resource_path(repo, "tests")):
        return None
    try:
        source = test_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return f"WORKFLOW_DEFERRED_MARKER_MISSING: unreadable {rel_path}"
    if not _source_declares_workflow_deferred(source, func_name):
        return f"WORKFLOW_DEFERRED_MARKER_MISSING: {node_id}"
    return None


def workflow_skip_pytest_module(repo: Path, module_relpath: str) -> bool:
    """True, если pytest workflow не должен собирать этот module path."""
    path = (resource_path(repo, module_relpath)).resolve()
    if not path.is_file():
        return False
    return is_requires_postgres_path(path, tests_root=resource_path(repo, "tests"))


def pytest_node_deferred_in_workflow(repo: Path, node_id: str) -> bool:
    """True, если workflow не должен поднимать pytest для этого node id.

    Сигналы (достаточно одного):
    - путь попадает в tier ``requires_postgres`` (канонические ``tests/scenarios/*``,
      кроме ``consultation-funnel-baseline``, и ``tests/integration/*``);
    - на целевой ``test_*`` явно стоит ``@pytest.mark.workflow_deferred``.
    """
    rel_path, func_name = _node_parts(node_id)
    test_path = (resource_path(repo, rel_path)).resolve()
    if not test_path.is_file():
        return False
    if is_requires_postgres_path(test_path, tests_root=resource_path(repo, "tests")):
        return True
    try:
        source = test_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return _source_declares_workflow_deferred(source, func_name)


def workflow_defer_blocks_supported_verdict(repo: Path, test_node_id: str) -> bool:
    """Блокирует machine SUPPORTED только при отсутствии маркера на defer-tier.

    Путь ``requires_postgres`` и явный ``workflow_deferred`` не отменяют
    структурный audit ``SUPPORTED``/``GAP`` — лишь требуют маркер при записи.
    """
    node = test_node_id.strip()
    if not node or "::" not in node:
        return False
    return validate_mapped_workflow_deferred_marker(repo, node) is not None


def repair_pytest_gate_machine_gap(pytest_receipt: dict[str, object] | None) -> bool:
    """Нужен ли machine GAP вместо read-only recheck после focused checks.

    ``DEFERRED`` (маркер есть, subprocess не поднимался) — не GAP, идём в recheck.
    ``PASS`` — gate пройден. ``BLOCKED``/``FAIL`` — GAP до recheck.
    """
    if not isinstance(pytest_receipt, dict):
        return False
    status = str(pytest_receipt.get("status") or "")
    if status in ("PASS", "DEFERRED"):
        return False
    return True


def repair_pytest_gate_passed(pytest_receipt: dict[str, object] | None) -> bool:
    """Пропуск/defer не проходит pytest-gate repair (только реальный PASS)."""
    if not isinstance(pytest_receipt, dict):
        return True
    return str(pytest_receipt.get("status") or "") == "PASS"


def partition_workflow_pytest_nodes(
    repo: Path, nodes: list[str],
) -> tuple[list[str], list[str]]:
    """Разделить node ids на исполняемые в workflow и отложенные (без subprocess)."""
    deferred: list[str] = []
    runnable: list[str] = []
    for node in nodes:
        if pytest_node_deferred_in_workflow(repo, node):
            deferred.append(node)
        else:
            runnable.append(node)
    return runnable, deferred
