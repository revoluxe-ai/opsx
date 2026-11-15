"""Переиспользование валидного APPLY-DESIGN на диске без вызова адаптера.

Повторное использование результата до вызова адаптера: валидный канон
переиспользуется без вызова адаптера. Не пишет в канон и не добавляет
``work_scope_digest`` в JSON design.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from workflows.artifact_paths import apply_design_artifact_path
from workflows.implementation.apply_design_hop_receipt import file_sha256

_SUBMIT_LOOP_CODES = frozenset({"DESIGN_INCOMPLETE", "DESIGN_MACHINE_GATE_FAILED"})
_SUBMIT_LOOP_BLOCK_CODE = "DESIGN_SUBMIT_LOOP_EXHAUSTED"
_MAX_IDENTICAL_SUBMIT_FAILS = 2

_REVIEW_REPAIR_STATUSES = frozenset(
    {
        "REPAIRS_REQUIRED",
        "DESIGN_NOT_READY",
        "PROTECTED_CONTEXT_DRIFT",
    },
)


def apply_design_canon_sha256(change_id: str, *, repo_root: Path) -> str:
    """SHA-256 канонического ``APPLY-DESIGN.json``; пустая строка, если файла нет."""
    path = apply_design_artifact_path(repo_root, change_id)
    if not path.is_file():
        return ""
    return file_sha256(path)


def unresolved_design_repair_intent(state: Mapping[str, Any]) -> bool:
    """True, когда требуется реальный design repair (нельзя переиспользовать канон).

    Отрицательный review, ``implementation_force_design_hop`` и явные recovery
    коды ремонта считаются намерением починить design. Повторные неполные submit
    при валидном каноне — не repair intent.
    """
    if state.get("implementation_force_design_hop"):
        return True
    review_status = str(state.get("apply_design_review_status") or "").strip()
    if review_status and review_status != "APPLY_DESIGN_REVIEW_PASS":
        if review_status in _REVIEW_REPAIR_STATUSES or review_status.endswith("_REQUIRED"):
            return True
    recovery = str(state.get("implementation_recovery_code") or "").strip()
    if recovery in _REVIEW_REPAIR_STATUSES:
        return True
    if recovery == "DESIGN_REDO":
        return True
    reject = str(state.get("implementation_plan_reject_code") or "").strip()
    if reject and reject not in _SUBMIT_LOOP_CODES and reject != "APPLY_DESIGN":
        if reject not in ("STALE_APPLY_DESIGN", "APPLY_BLOCKED"):
            errors = state.get("implementation_machine_gate_errors")
            if isinstance(errors, list) and errors:
                return True
    if int(state.get("implementation_hop_machine_repair_count") or 0) > 0:
        errors = state.get("implementation_machine_gate_errors")
        if isinstance(errors, list) and errors:
            for err in errors:
                if not isinstance(err, dict):
                    continue
                code = str(err.get("code") or "")
                if code and code not in _SUBMIT_LOOP_CODES:
                    return True
    return False


def can_reuse_valid_apply_design_canon(
    *,
    gate_ok: bool,
    snapshot_ok: bool,
    artifact_present: bool,
    repair_intent: bool,
    submit_fail_streak: int = 0,
) -> bool:
    """Можно ли завершить hop без вызова адаптера, оставив байты канона неизменными.

    Повторное использование результата до вызова адаптера допустимо только при
    нулевом streak идентичных fail submit на текущей ревизии канона.
    """
    if repair_intent:
        return False
    if int(submit_fail_streak) != 0:
        return False
    return artifact_present and gate_ok and snapshot_ok


def design_submit_loop_blocked(
    state: Mapping[str, Any],
    *,
    change_id: str,
    repo_root: Path,
    next_fail_code: str | None,
) -> tuple[bool, str | None]:
    """После двух одинаковых fail на той же ревизии канона — diagnosable BLOCKED.

    Возвращает ``(blocked, block_code)``. Не повышает таймауты и retry count.
    """
    if next_fail_code not in _SUBMIT_LOOP_CODES:
        return False, None
    canon_sha = apply_design_canon_sha256(change_id, repo_root=repo_root)
    if not canon_sha:
        return False, None
    tracked_sha = str(state.get("implementation_design_canon_sha256") or "")
    streak = int(state.get("implementation_design_submit_fail_streak") or 0)
    last_code = str(state.get("implementation_design_submit_fail_code") or "")
    if tracked_sha == canon_sha and last_code == next_fail_code and streak >= _MAX_IDENTICAL_SUBMIT_FAILS:
        return True, _SUBMIT_LOOP_BLOCK_CODE
    return False, None


def patch_after_failed_submit(
    *,
    state: Mapping[str, Any],
    change_id: str,
    repo_root: Path,
    fail_code: str | None,
) -> dict[str, Any]:
    """Обновить счётчик идентичных fail submit на той же ревизии канона."""
    if fail_code not in _SUBMIT_LOOP_CODES:
        return {
            "implementation_design_submit_fail_streak": 0,
            "implementation_design_submit_fail_code": None,
            "implementation_design_canon_sha256": apply_design_canon_sha256(
                change_id, repo_root=repo_root
            )
            or None,
        }
    canon_sha = apply_design_canon_sha256(change_id, repo_root=repo_root)
    tracked_sha = str(state.get("implementation_design_canon_sha256") or "")
    last_code = str(state.get("implementation_design_submit_fail_code") or "")
    streak = int(state.get("implementation_design_submit_fail_streak") or 0)
    if tracked_sha == canon_sha and last_code == fail_code:
        streak += 1
    else:
        streak = 1
    return {
        "implementation_design_canon_sha256": canon_sha or None,
        "implementation_design_submit_fail_streak": streak,
        "implementation_design_submit_fail_code": fail_code,
    }


def reset_design_submit_fail_tracking(change_id: str, *, repo_root: Path) -> dict[str, Any]:
    """Сбросить счётчик после успешного reuse или принятого submit."""
    return {
        "implementation_design_canon_sha256": apply_design_canon_sha256(change_id, repo_root=repo_root)
        or None,
        "implementation_design_submit_fail_streak": 0,
        "implementation_design_submit_fail_code": None,
    }
