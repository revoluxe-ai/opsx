"""Нумерованный снимок mapped test для auditor prompt и scoped read paging."""

from __future__ import annotations

from opsx.project_context import resource_path

import ast
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

_FUTURE_IMPORT = re.compile(r"^\s*from\s+__future__\s+import\s+")


def file_source_revision(source: str) -> str:
    """Стабильный digest содержимого файла (тот же снимок, что у proof validator)."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _parse_node_id(test_node_id: str) -> tuple[str, str]:
    if "::" not in test_node_id:
        raise ValueError(f"TEST_NODE_INVALID: {test_node_id}")
    rel, func = test_node_id.split("::", 1)
    func = func.split("[", 1)[0]
    return rel.replace("\\", "/"), func


def _function_bounds(source: str, func_name: str, *, filename: str) -> tuple[int, int]:
    tree = ast.parse(source, filename=filename)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name
    ]
    if len(matches) != 1:
        raise ValueError(f"TEST_NODE_NOT_FOUND: {func_name}")
    fn = matches[0]
    return fn.lineno, fn.end_lineno or fn.lineno


def format_numbered_lines(
    lines: list[str],
    *,
    first_line: int = 1,
) -> str:
    """Сформировать nl-подобный вид с абсолютными 1-based номерами (view, не запись в файл)."""
    if not lines:
        return ""
    last_line = first_line + len(lines) - 1
    width = max(4, len(str(last_line)))
    parts: list[str] = []
    for offset, line in enumerate(lines):
        lineno = first_line + offset
        body = line.rstrip("\n\r")
        parts.append(f"{lineno:>{width}}| {body}")
    return "\n".join(parts) + "\n"


def format_numbered_page(
    source: str,
    *,
    offset: int = 1,
    limit: int | None = None,
) -> tuple[str, int, int, int]:
    """Вернуть numbered fragment и (total_lines, start, end) для paging read."""
    if offset < 1:
        raise ValueError("OFFSET_INVALID")
    lines = source.splitlines(keepends=True)
    if not lines and source:
        lines = [source]
    total = len(lines) if lines or not source else 0
    if not lines:
        return "", total, 0, 0
    end = total if limit is None else min(total, offset - 1 + limit)
    if offset > total:
        return "", total, offset, offset - 1
    chunk = lines[offset - 1 : end]
    text = format_numbered_lines([line.rstrip("\n\r") for line in chunk], first_line=offset)
    return text, total, offset, end


def mapped_test_snapshot(repo_root: Path, test_node_id: str) -> dict[str, Any]:
    """Полный numbered snapshot mapped test для ledger/prompt (без записи в файл)."""
    rel, func_name = _parse_node_id(test_node_id)
    path = (resource_path(repo_root, rel)).resolve()
    if not path.is_file():
        return {
            "mapped_test_path": rel,
            "mapped_test_function": func_name,
            "mapped_test_syntax_error": f"MAPPED_TEST_MISSING: {rel}",
        }
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {
            "mapped_test_path": rel,
            "mapped_test_function": func_name,
            "mapped_test_syntax_error": f"MAPPED_TEST_UNREADABLE: {exc}",
        }
    revision = file_source_revision(source)
    lines = source.splitlines()
    payload: dict[str, Any] = {
        "mapped_test_path": rel,
        "mapped_test_function": func_name,
        "mapped_test_source_revision": revision,
        "mapped_test_total_lines": len(lines),
    }
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        payload["mapped_test_syntax_error"] = (
            f"MAPPED_TEST_SYNTAX_ERROR: {exc.msg} (line {exc.lineno})"
        )
        payload["mapped_test_numbered_source"] = format_numbered_lines(lines)
        return payload
    try:
        first, last = _function_bounds(source, func_name, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        payload["mapped_test_syntax_error"] = str(exc)
        payload["mapped_test_numbered_source"] = format_numbered_lines(lines)
        return payload
    payload["mapped_test_function_first_line"] = first
    payload["mapped_test_function_last_line"] = last
    payload["mapped_test_numbered_source"] = format_numbered_lines(lines)
    return payload


def enrich_ledger_mapped_test(repo_root: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Дополнить ledger-запись numbered source и границами mapped function."""
    merged = dict(entry)
    node = str(entry.get("test_node_id") or "").strip()
    if not node:
        return merged
    merged.update(mapped_test_snapshot(repo_root, node))
    return merged
