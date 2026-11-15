"""Канонические пути machine receipts scenario-coverage в pytest-фикстурах."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.common.scenario_files import (
    plan_receipt_machine_path,
    row_walk_journal_machine_path,
    trace_matrix_machine_path,
)


def ensure_machine_receipt_dirs(coverage: Path) -> None:
    """Создать ``opsx/receipts/<change>/scenario-coverage`` для фикстур."""
    plan_receipt_path(coverage).parent.mkdir(parents=True, exist_ok=True)


def plan_receipt_path(coverage: Path) -> Path:
    """``plan.receipt.json`` под ``opsx/receipts`` для каталога ``scenario-coverage``."""
    return plan_receipt_machine_path(coverage)


def trace_matrix_path(coverage: Path) -> Path:
    """``test_trace_matrix.json`` под ``opsx/receipts``."""
    return trace_matrix_machine_path(coverage)


def row_journal_path(coverage: Path) -> Path:
    """``row_walk_journal.json`` под ``opsx/receipts``."""
    return row_walk_journal_machine_path(coverage)


def write_plan_receipt(coverage: Path, text: str) -> Path:
    """Записать plan receipt с созданием родительских каталогов."""
    path = plan_receipt_path(coverage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_trace_matrix(
    coverage: Path,
    payload: dict[str, Any] | str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Записать trace matrix (dict сериализуется в JSON)."""
    path = trace_matrix_path(coverage)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding=encoding)
    else:
        path.write_text(payload, encoding=encoding)
    return path


def write_row_journal(coverage: Path, payload: dict[str, Any] | str) -> Path:
    """Записать row walk journal."""
    path = row_journal_path(coverage)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")
    return path


__all__ = [
    "ensure_machine_receipt_dirs",
    "plan_receipt_path",
    "row_journal_path",
    "trace_matrix_path",
    "write_plan_receipt",
    "write_row_journal",
    "write_trace_matrix",
]
