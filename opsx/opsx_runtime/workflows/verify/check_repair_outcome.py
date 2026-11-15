"""Исход verify check-repair hop и маршрутизация subgraph (не безусловный re-discovery).

Типизированный результат repair hop для ``check_workflow``: PAUSE vs re-verify vs
адресный retry без полного discovery hunt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows.repair.schemas import FindingReport, RepairState

WorkflowOutcome = Literal["SUCCESS", "VERIFY_PAUSED", "VERIFY_FAILED"]
NextAllowed = Literal[
    "RE_VERIFY",
    "ADDRESS_RETRY",
    "DIAGNOSTIC_READONLY",
    "ESCALATE",
    "NONE",
]
StopKind = Literal[
    "RESOURCE_LIMIT",
    "TEST_SETUP_DEFECT",
    "PRODUCT_DEFECT",
    "INCONCLUSIVE",
    "NO_PROGRESS",
    "NONE",
]


class CheckRepairHopOutcome(BaseModel):
    """Machine-readable исход одного nested bug-team repair в verify-check subgraph."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str | None = None
    stage: str = "repair_mission"
    terminal_status: str
    stop_kind: StopKind = "NONE"
    workflow_outcome: WorkflowOutcome
    next_allowed: NextAllowed
    repair_mission_id: str | None = None
    changed_files: bool = False
    task_budget_remaining: int | None = None
    continuation_id: str | None = None
    error: str | None = None


def state_key_prefix_for_check(name_prefix: str) -> str:
    """Префикс GraphState для check-repair (security ≠ project_bug)."""
    if name_prefix == "security":
        return "security"
    return "bug"


def _resource_limit_error(error: str | None, final: RepairState) -> bool:
    if not error:
        for run in reversed(final.runs):
            if run.input_limit_exceeded:
                return True
            if run.budget_stop_reason == "RESOURCE_LIMIT":
                return True
    if error:
        lowered = error.lower()
        if "input-token limit" in lowered or "resource_limit" in lowered:
            return True
    return False


def _mission_changed_files(final: RepairState) -> bool:
    for outcome in final.finished:
        fix = outcome.fix
        if fix is not None and fix.files_changed:
            return True
    return False


def _task_budget_remaining(output_dir: str | None) -> int | None:
    if not output_dir:
        return None
    path = Path(output_dir) / "task_budget_ledger.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cap = int(data.get("max_observed_input_per_task") or 0)
    if cap <= 0:
        return None
    observed = int(data.get("accumulated_observed_input") or 0)
    estimated = int(data.get("accumulated_estimated_input") or 0)
    return max(0, cap - observed - estimated)


def _address_retry_meta_path(output_dir: str) -> Path:
    return Path(output_dir) / "repair_address_retry.json"


def _address_retry_count(output_dir: str, repair_task_id: str) -> int:
    path = _address_retry_meta_path(output_dir)
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if str(data.get("repair_task_id") or "") != repair_task_id:
        return 0
    return int(data.get("count") or 0)


def record_address_retry(output_dir: str, repair_task_id: str, continuation_id: str) -> None:
    """Увеличить счётчик адресных retry для task (OPSX durable, не LangGraph only)."""
    path = _address_retry_meta_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = _address_retry_count(output_dir, repair_task_id) + 1
    path.write_text(
        json.dumps(
            {
                "repair_task_id": repair_task_id,
                "continuation_id": continuation_id,
                "count": count,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def compute_repair_task_id(
    report: FindingReport,
    *,
    harness_profile: str = "production_like",
) -> str:
    """Стабильный id verify-repair задачи (не сбрасывается при product fix)."""
    finding_id = ""
    if report.findings:
        finding_id = report.findings[0].id or report.findings[0].location
    requirement_fp = hashlib.sha256(
        f"{finding_id}:{harness_profile}".encode("utf-8"),
    ).hexdigest()[:16]
    raw = f"{finding_id}|{requirement_fp}|{harness_profile}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def derive_check_repair_hop_outcome(
    final: RepairState,
    *,
    report: FindingReport,
    repair_task_id: str,
) -> CheckRepairHopOutcome:
    """Построить исход repair hop из terminal ``RepairState``."""
    terminal = str(final.status or "UNKNOWN")
    error = final.error
    changed = _mission_changed_files(final)
    finding_id = report.findings[0].id if report.findings else None

    if terminal in {
        "COMPLETE_NO_FINDINGS",
        "COMPLETE_NO_REPAIR",
        "FAMILY_CONVERGED",
        "CLOSED",
    } and not error:
        return CheckRepairHopOutcome(
            finding_id=finding_id,
            terminal_status=terminal,
            workflow_outcome="SUCCESS",
            next_allowed="NONE",
            repair_mission_id=final.mission_id,
            changed_files=changed,
        )

    if _resource_limit_error(error, final):
        remaining = _task_budget_remaining(final.output_dir)
        retries = _address_retry_count(final.output_dir, repair_task_id)
        min_reserve = 500_000
        eligible = (
            remaining is not None
            and remaining >= min_reserve
            and retries < 2
        )
        if eligible:
            return CheckRepairHopOutcome(
                finding_id=finding_id,
                terminal_status="BLOCKED",
                stop_kind="RESOURCE_LIMIT",
                workflow_outcome="VERIFY_PAUSED",
                next_allowed="ADDRESS_RETRY",
                repair_mission_id=final.mission_id,
                changed_files=changed,
                task_budget_remaining=remaining,
                continuation_id=f"resource-retry:{repair_task_id}",
                error=error,
            )
        return CheckRepairHopOutcome(
            finding_id=finding_id,
            terminal_status="BLOCKED",
            stop_kind="RESOURCE_LIMIT",
            workflow_outcome="VERIFY_PAUSED",
            next_allowed="NONE",
            repair_mission_id=final.mission_id,
            changed_files=changed,
            task_budget_remaining=remaining,
            error=error or "RESOURCE_LIMIT_EXHAUSTED",
        )

    if error or terminal in {"BLOCKED", "BLOCKED_INCONCLUSIVE", "FAILED"}:
        return CheckRepairHopOutcome(
            finding_id=finding_id,
            terminal_status=terminal,
            stop_kind="INCONCLUSIVE",
            workflow_outcome="VERIFY_PAUSED",
            next_allowed="NONE",
            repair_mission_id=final.mission_id,
            changed_files=changed,
            error=error,
        )

    if changed:
        return CheckRepairHopOutcome(
            finding_id=finding_id,
            terminal_status=terminal,
            workflow_outcome="SUCCESS",
            next_allowed="RE_VERIFY",
            repair_mission_id=final.mission_id,
            changed_files=True,
        )

    return CheckRepairHopOutcome(
        finding_id=finding_id,
        terminal_status=terminal,
        workflow_outcome="SUCCESS",
        next_allowed="NONE",
        repair_mission_id=final.mission_id,
        changed_files=changed,
        error=error,
    )


def outcome_to_graph_patch(
    outcome: CheckRepairHopOutcome,
    *,
    state_key_prefix: str,
) -> dict[str, Any]:
    """GraphState patch после repair hop."""
    base = f"{state_key_prefix}_check_repair"
    patch: dict[str, Any] = {
        f"{base}_workflow_outcome": outcome.workflow_outcome,
        f"{base}_next_allowed": outcome.next_allowed,
        f"{base}_stop_kind": outcome.stop_kind,
        "verify_workflow_paused": outcome.workflow_outcome == "VERIFY_PAUSED",
    }
    if outcome.workflow_outcome == "VERIFY_PAUSED":
        patch["verify_pause_reason"] = outcome.error or outcome.stop_kind
    return patch


def route_after_check_repair(state: dict[str, Any], name_prefix: str) -> str:
    """Куда идти после repair_node: discovery, repair retry, или END (PAUSE)."""
    sk = state_key_prefix_for_check(name_prefix)
    next_allowed = str(state.get(f"{sk}_check_repair_next_allowed") or "NONE")
    workflow_outcome = str(state.get(f"{sk}_check_repair_workflow_outcome") or "")

    discovery_node = f"{name_prefix}_discovery"
    repair_node = f"{name_prefix}_check_repair"

    if workflow_outcome == "VERIFY_PAUSED" and next_allowed == "NONE":
        return "END"
    if next_allowed == "ADDRESS_RETRY":
        return repair_node
    if next_allowed == "RE_VERIFY":
        return discovery_node
    if workflow_outcome == "SUCCESS" and next_allowed == "NONE":
        return discovery_node
    return discovery_node
