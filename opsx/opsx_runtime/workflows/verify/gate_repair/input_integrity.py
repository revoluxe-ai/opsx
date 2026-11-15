"""Детерминированная полнота gate diagnostics (pytest + mypy) для group_error handoff."""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping

from workflows.verify.gate_repair.schemas import GateCheckResult

InputIntegrityStatus = Literal["COMPLETE", "PARTIAL", "INVALID"]

_PRECEDENCE: dict[InputIntegrityStatus, int] = {
    "INVALID": 3,
    "PARTIAL": 2,
    "COMPLETE": 1,
}


def is_pytest_structured_gate_evidence(error: str | None) -> bool:
    """True для JSON stall/suite evidence в ``GateCheckResult.error`` (не subprocess crash).

    ``PYTEST_KILL_FAILED`` и невалидный JSON остаются реальными gate errors.
    """
    if not error or not error.strip():
        return False
    try:
        payload = json.loads(error)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    kind = payload.get("kind")
    if kind == "PYTEST_KILL_FAILED":
        return False
    if "last_node_id" in payload and (
        "timeout_s" in payload or "stall_limit_s" in payload
    ):
        return True
    return False


def assess_pytest_input_integrity(
    check: GateCheckResult,
    *,
    parsed_count: int,
    log_text: str,
) -> tuple[InputIntegrityStatus, list[str]]:
    """Оценить полноту pytest-части ``quality_gate_diagnostics`` (не verdict gates).

    Args:
        check: Subprocess metadata pytest gate.
        parsed_count: Число распарсенных pytest observations.
        log_text: Полный stdout+stderr pytest для проверки непустого лога.
    """
    issues: list[str] = []
    if check.error and not is_pytest_structured_gate_evidence(check.error):
        detail = check.error if len(check.error) <= 240 else f"{check.error[:237]}..."
        return "INVALID", [f"pytest gate subprocess error: {detail}"]
    if check.exit_code == 124:
        return "INVALID", ["pytest gate timed out"]
    if check.exit_code == 127 and not (check.stdout_tail or "").strip():
        detail = (check.stderr_tail or "command failed")[:240]
        return "INVALID", [f"pytest gate failed to execute: {detail}"]

    if check.exit_code == 0:
        if parsed_count != 0:
            issues.append("pytest exit 0 but parsed failure observations present")
            return "PARTIAL", issues
        return "COMPLETE", []

    if parsed_count > 0:
        return "COMPLETE", []
    if check.error and is_pytest_structured_gate_evidence(check.error):
        try:
            payload = json.loads(check.error)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            node = str(payload.get("last_node_id") or "").strip()
            if node and node != "UNKNOWN_NODE" and ".py" in node:
                return "COMPLETE", []
            if node == "UNKNOWN_NODE" or not node:
                from workflows.verify.gate_repair.diagnostics import (
                    last_pytest_node_id_from_progress_text,
                )

                tail = str(payload.get("stdout_tail") or "") or log_text
                if last_pytest_node_id_from_progress_text(tail):
                    return "COMPLETE", []
    stripped = log_text.strip()
    if stripped:
        if "collection failed" in stripped.lower() or "error collecting" in stripped.lower():
            return "COMPLETE", []
        issues.append(
            "pytest exit non-zero but no FAILED/ERROR node ids parsed from log",
        )
        return "PARTIAL", issues
    return "INVALID", ["pytest failed with empty log; diagnostics completeness not provable"]


def merge_input_integrity_status(
    *pairs: tuple[InputIntegrityStatus, list[str]],
) -> tuple[InputIntegrityStatus, list[str]]:
    """Свести статусы инструментов: INVALID > PARTIAL > COMPLETE; issues объединяются."""
    if not pairs:
        return "PARTIAL", ["no input integrity assessments"]
    status: InputIntegrityStatus = "COMPLETE"
    issues: list[str] = []
    for item_status, item_issues in pairs:
        if _PRECEDENCE[item_status] > _PRECEDENCE[status]:
            status = item_status
        for issue in item_issues:
            if issue not in issues:
                issues.append(issue)
    return status, issues


def count_handoff_pytest_node_ids(pytest_observations: list[str]) -> int:
    """Число распознаваемых pytest node id в handoff-списке ``quality_gate_diagnostics.pytest``."""
    from workflows.verify.gate_repair.pytest_failure_excerpt import (
        node_id_from_pytest_observation,
    )

    count = 0
    for item in pytest_observations:
        if node_id_from_pytest_observation(str(item)):
            count += 1
    return count


def _integrity_blockers_allow_pytest_promotion(issues: list[str]) -> bool:
    """True, если PARTIAL/INVALID не из-за пустого pytest-лога без node id."""
    for issue in issues:
        lowered = issue.lower()
        if "pytest failed with empty log" in lowered:
            return False
        if lowered.startswith("pytest gate subprocess error"):
            return False
        if lowered.startswith("pytest gate timed out"):
            return False
        if lowered.startswith("pytest gate failed to execute"):
            return False
    return True


def promote_integrity_when_pytest_nodes_present(
    status: InputIntegrityStatus,
    issues: list[str],
    *,
    pytest_exit_nonzero: bool,
    pytest_node_count: int,
) -> tuple[InputIntegrityStatus, list[str]]:
    """Поднять integrity до COMPLETE при именованных pytest FAIL (аналог mixed-scope mypy).

    Пустой fail-log без node id остаётся PARTIAL/INVALID.
    """
    if status == "COMPLETE":
        return status, issues
    if not pytest_exit_nonzero or pytest_node_count <= 0:
        return status, issues
    if not _integrity_blockers_allow_pytest_promotion(issues):
        return status, issues
    promoted_issues = list(issues)
    note = (
        "input_integrity promoted to COMPLETE: handoff contains "
        f"{pytest_node_count} parsed pytest node id(s) for non-zero pytest exit"
    )
    if note not in promoted_issues:
        promoted_issues.append(note)
    return "COMPLETE", promoted_issues


def reconcile_handoff_input_integrity(
    diagnostics: Mapping[str, Any] | None,
    *,
    pytest_exit_nonzero: bool | None = None,
) -> tuple[InputIntegrityStatus, list[str]]:
    """Пересчитать integrity из persisted handoff (group_error / repair resume)."""
    if not diagnostics:
        return "INVALID", ["quality_gate_diagnostics missing"]
    status_raw = diagnostics.get("input_integrity_status")
    issues_raw = diagnostics.get("input_integrity_issues")
    status: InputIntegrityStatus = (
        status_raw if status_raw in _PRECEDENCE else "PARTIAL"
    )
    issues: list[str] = (
        [str(item) for item in issues_raw if str(item).strip()]
        if isinstance(issues_raw, list)
        else []
    )
    pytest_raw = diagnostics.get("pytest")
    observations = (
        [str(item) for item in pytest_raw if str(item).strip()]
        if isinstance(pytest_raw, list)
        else []
    )
    node_count = count_handoff_pytest_node_ids(observations)
    if pytest_exit_nonzero is None:
        pytest_exit_nonzero = node_count > 0 or bool(observations)
    return promote_integrity_when_pytest_nodes_present(
        status,
        issues,
        pytest_exit_nonzero=pytest_exit_nonzero,
        pytest_node_count=node_count,
    )
