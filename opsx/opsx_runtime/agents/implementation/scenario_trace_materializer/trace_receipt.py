"""PRE/POST идемпотентность materializer: digest входов в ``test_trace_matrix.json``.

Использует ``CheckWorkflowReceipt`` по образцу slice-stage; матрица — единственный
durable receipt, без второго JSON.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from check_workflow_receipt import CheckWorkflowReceipt, CheckWorkflowReceiptStatus

_CHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

WORK_SCOPE_DIGEST_KEY = "work_scope_digest"


def _change_prefix(change_id: str) -> str:
    return f"openspec/changes/{change_id}"


def trace_input_relative_paths(change_id: str) -> tuple[str, ...]:
    """Относительные пути scope digest (без ``test_trace_matrix.json``)."""
    base = _change_prefix(change_id)
    return (
        f"{base}/scenario-coverage/plan.md",
        f"opsx/receipts/{change_id}/scenario-coverage/plan.receipt.json",
        f"{base}/specs",
        f"{base}/scenario-coverage/scenarios",
        "tests",
    )


def build_trace_scope_receipt(change_id: str) -> CheckWorkflowReceipt:
    """Scope receipt для входов materializer (только plan/spec/scenario/tests)."""
    return CheckWorkflowReceipt(
        None,
        trace_input_relative_paths(change_id),
        include_change_artifacts=False,
        exclude_tests_opsx=False,
    )


def _digest_state(repo_root: Path, change_id: str) -> dict[str, Any]:
    return {
        "repository_path": str(repo_root.resolve()),
        "change_id": change_id,
    }


def compute_trace_work_scope_digest(repo_root: Path, change_id: str) -> str | None:
    """Текущий digest входов materializer или ``None`` если scope пуст/недоступен."""
    if not _CHANGE.fullmatch(change_id):
        return None
    return build_trace_scope_receipt(change_id)._digest(_digest_state(repo_root, change_id))


def trace_scope_digest_current(
    stored_digest: str,
    repo_root: Path,
    change_id: str,
) -> bool:
    """``True``, если pin на диске совпадает с сохранённым ``work_scope_digest``."""
    text = str(stored_digest or "").strip()
    if len(text) != 64:
        return False
    current = compute_trace_work_scope_digest(repo_root, change_id)
    if not current:
        return False
    if current == text:
        return True
    receipt = build_trace_scope_receipt(change_id)
    root = repo_root.resolve()
    try:
        paths = receipt._collect_paths(root, _digest_state(repo_root, change_id))
        if paths:
            return CheckWorkflowReceipt.digest_matches(text, root, paths)
    except (OSError, ValueError):
        pass
    return False


def read_matrix_work_scope_digest(matrix_path: Path) -> str | None:
    """Прочитать ``work_scope_digest`` из существующей матрицы."""
    if not matrix_path.is_file() or matrix_path.is_symlink():
        return None
    try:
        obj = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    stored = obj.get(WORK_SCOPE_DIGEST_KEY)
    return str(stored).strip() if isinstance(stored, str) and stored.strip() else None


def trace_receipt_pre_skip(
    repo_root: Path,
    change_id: str,
    *,
    verify_preflight: Mapping[str, Any],
    matrix_path: Path,
) -> bool:
    """``True`` только при verify READY, digest MATCH и матрица на диске."""
    if str(verify_preflight.get("status") or "") != "READY":
        return False
    if not matrix_path.is_file():
        return False
    stored = read_matrix_work_scope_digest(matrix_path)
    if not stored:
        return False
    return trace_scope_digest_current(stored, repo_root, change_id)


def trace_receipt_check_status(
    stored_digest: str | None,
    repo_root: Path,
    change_id: str,
) -> CheckWorkflowReceiptStatus | None:
    """Статус pin относительно текущего scope (без ``cycle_checks`` в state)."""
    text = str(stored_digest or "").strip()
    if not text:
        return CheckWorkflowReceiptStatus.NOT_ESTABLISHED
    if trace_scope_digest_current(text, repo_root, change_id):
        return CheckWorkflowReceiptStatus.MATCH
    return CheckWorkflowReceiptStatus.STALE
