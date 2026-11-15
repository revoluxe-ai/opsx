"""Структурированные INFO-логи маршрутизации verify и gate-repair узлов."""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.state import GraphState

log = logging.getLogger("opsx.verify.routing")


def quality_gate_snapshot(state: GraphState) -> dict[str, Any]:
    """Снимок полей quality gates из state для маршрутизации и skip-решений."""
    diag = state.get("quality_gate_diagnostics")
    total: int | None = None
    if isinstance(diag, dict):
        raw_total = diag.get("total_count")
        if isinstance(raw_total, int):
            total = raw_total
    return {
        "quality_gate_verdict": str(state.get("quality_gate_verdict") or ""),
        "quality_gates_execution_status": str(state.get("quality_gates_execution_status") or ""),
        "diagnostics_total_count": total,
        "verify_loop_status": str(state.get("verify_loop_status") or ""),
        "static_repair_status": str(state.get("static_repair_status") or ""),
    }


def quality_gate_issue_lines(state: GraphState, *, max_per_tool: int = 8) -> list[str]:
    """Строки pytest/pyright/mypy из ``quality_gate_diagnostics`` для логов остановки."""
    diag = state.get("quality_gate_diagnostics")
    if not isinstance(diag, dict):
        return []
    lines: list[str] = []
    for tool in ("pytest", "pyright", "mypy"):
        raw = diag.get(tool)
        if not isinstance(raw, list):
            continue
        for item in raw[:max_per_tool]:
            if isinstance(item, str) and item.strip():
                lines.append(f"{tool}: {item.strip()}")
    return lines


def log_gate_repair_stop(state: GraphState, *, stop_kind: str, source: str) -> None:
    """INFO: почему gate-repair subgraph останавливается (cap, pause, END без green)."""
    from workflows.verify.outer_loop import resolve_outer_max_iterations

    iteration = int(state.get("verify_outer_iteration") or 0)
    max_iter = resolve_outer_max_iterations(state)
    clean, clean_reason = explain_is_verify_clean(state)
    issues = quality_gate_issue_lines(state)
    owner = state.get("owner_attention")
    owner_note = str(owner).strip() if isinstance(owner, str) and owner.strip() else None
    log.info(
        "verify gate_repair STOP stop_kind=%s source=%s outer_iteration=%s outer_max=%s "
        "is_verify_clean=%s clean_reason=%s verify_workflow_code_changed=%s "
        "owner_attention=%s remaining_gate_issues=%s snapshot=%s",
        stop_kind,
        source,
        iteration,
        max_iter,
        clean,
        clean_reason,
        state.get("verify_workflow_code_changed"),
        owner_note,
        issues if issues else ["(no parsed gate issue lines in quality_gate_diagnostics)"],
        quality_gate_snapshot(state),
    )


def explain_is_verify_clean(state: GraphState) -> tuple[bool, str]:
    """Вычислить ``is_verify_clean`` и человекочитаемую причину (для логов, без side effects)."""
    from workflows.verify.outer_loop import quality_gate_actionable_fail

    exec_status = str(state.get("quality_gates_execution_status") or "").strip()
    if not exec_status:
        return False, "quality_gates_execution_status_missing"
    if not quality_gate_actionable_fail(state):
        return True, "quality_gate_no_actionable_failures"
    verdict = str(state.get("quality_gate_verdict") or "")
    if verdict != "PASS":
        if verdict:
            return False, f"quality_gate_verdict={verdict}"
        return False, "quality_gate_verdict_missing"
    diag = state.get("quality_gate_diagnostics")
    total: int | None = None
    if isinstance(diag, dict):
        raw_total = diag.get("total_count")
        if isinstance(raw_total, int):
            total = raw_total
    if total is None:
        return False, "quality_gate_diagnostics_missing"
    if total == 0:
        return True, "quality_gate_diagnostics.total_count=0"
    analysis = state.get("analysis_result")
    if isinstance(analysis, dict):
        findings = analysis.get("findings")
        summary = analysis.get("triage_summary")
        if isinstance(findings, list) and not findings:
            if isinstance(summary, dict) and summary.get("findings_total") == 0:
                return True, "analysis_result_empty_findings"
    return False, "quality_gates_not_clean"


def log_outer_cycle_begin_route(state: GraphState, *, target: str) -> None:
    """Залогировать решение ``route_after_outer_cycle_begin`` (group_error vs skip)."""
    clean, reason = explain_is_verify_clean(state)
    snap = quality_gate_snapshot(state)
    log.info(
        "verify gate_repair route_after_outer_cycle_begin target=%s is_verify_clean=%s reason=%s "
        "outer_iteration=%s snapshot=%s",
        target,
        clean,
        reason,
        int(state.get("verify_outer_iteration") or 0),
        snap,
    )


def log_reset_repair_batch_route(state: GraphState, *, target: str) -> None:
    """Залогировать исход ``route_after_reset_repair_batch``."""
    log.info(
        "verify gate_repair route_after_reset_repair_batch target=%s "
        "verify_loop_terminal=%s verify_post_reset_route=%s "
        "verify_cohort_restart_count=%s owner_attention=%s",
        target,
        state.get("verify_loop_terminal"),
        state.get("verify_post_reset_route"),
        int(state.get("verify_cohort_restart_count") or 0),
        str(state.get("owner_attention") or "")[:240],
    )


def log_group_pass_route(state: GraphState, *, target: str) -> None:
    """Залогировать решение ``route_after_group_pass``."""
    clean, reason = explain_is_verify_clean(state)
    loop_status = str(state.get("verify_loop_status") or "")
    log.info(
        "verify gate_repair route_after_group_pass target=%s verify_loop_terminal=%s "
        "verify_loop_status=%s is_verify_clean=%s reason=%s outer_iteration=%s "
        "remaining_gate_issues=%s",
        target,
        state.get("verify_loop_terminal"),
        loop_status,
        clean,
        reason,
        int(state.get("verify_outer_iteration") or 0),
        quality_gate_issue_lines(state)
        if not clean or loop_status == "VERIFY_LOOP_CAP"
        else [],
    )


def log_impl_verify_post_route(state: GraphState, *, target: str) -> None:
    """Залогировать исход conditional edge после ``impl_verify``."""
    log.info(
        "verify route_after_impl_verify target=%s verdict=%s marker=%s "
        "verify_green_restart_count=%s",
        target,
        str(state.get("verdict") or ""),
        str(state.get("marker") or ""),
        int(state.get("verify_green_restart_count") or 0),
    )


def log_scenario_coverage_post_route(state: GraphState, *, target: str) -> None:
    """Залогировать исход conditional edge после ``scenario_coverage_verify``."""
    log.info(
        "verify route_after_scenario_coverage target=%s scenario_coverage_verdict=%s "
        "marker=%s",
        target,
        str(state.get("scenario_coverage_verdict") or ""),
        str(state.get("scenario_coverage_marker") or ""),
    )


def log_coverage_activation_node_start(state: GraphState) -> None:
    """INFO при входе в узел ``coverage_activation`` (``opsx.verify.routing`` → stderr)."""
    log.info(
        "verify coverage_activation node=start change_id=%s repository=%s",
        str(state.get("change_id") or ""),
        str(state.get("repository_path") or state.get("workspace") or ""),
    )


def log_quality_gates_pytest_start(
    state: GraphState,
    *,
    scope: str,
    timeout_s: float,
    test_count: int,
    per_test_s: float,
) -> None:
    """INFO перед pytest run в ``quality_gates`` / ``pytest_execute`` (после collect)."""
    log.info(
        "verify quality_gates pytest_start change_id=%s scope=%s timeout_s=%s "
        "test_count=%s per_test_s=%s",
        str(state.get("change_id") or ""),
        scope,
        f"{timeout_s:.0f}",
        str(test_count),
        f"{per_test_s:.0f}",
    )


def log_scenario_test_repair_post_route(state: GraphState, *, target: str) -> None:
    """Залогировать исход conditional edge после ``scenario_test_repair``."""
    log.info(
        "verify route_after_scenario_test_repair target=%s scenario_test_repair_status=%s "
        "changed_paths=%s",
        target,
        str(state.get("scenario_test_repair_status") or ""),
        state.get("scenario_test_repair_changed_paths"),
    )


def log_static_repair_post_route(state: GraphState, *, target: str) -> None:
    """Залогировать исход conditional edge после compiled ``static_repair`` (gate_repair)."""
    loop_status = str(state.get("verify_loop_status") or "")
    green = loop_status == "VERIFY_OK"
    log.info(
        "verify route_after_static_repair target=%s verify_loop_status=%s "
        "gate_repair_green=%s verify_workflow_code_changed=%s "
        "remaining_gate_issues=%s snapshot=%s",
        target,
        loop_status,
        green,
        state.get("verify_workflow_code_changed"),
        quality_gate_issue_lines(state) if not green else [],
        quality_gate_snapshot(state),
    )
    if loop_status == "VERIFY_LOOP_CAP":
        log_gate_repair_stop(
            state,
            stop_kind="VERIFY_LOOP_CAP",
            source="route_after_static_repair",
        )


def log_dispatch_node_start(state: GraphState, *, node: str) -> float:
    """Начало тела узла dispatch-cycle (static triage / test repair); вернуть monotonic t0."""
    item = state.get("dispatcher_current_item")
    finding_id = item.get("finding_id") if isinstance(item, dict) else None
    log.info(
        "verify dispatch_cycle node=%s phase=start finding_id=%s dispatcher_path=%s",
        node,
        finding_id,
        str(state.get("verify_dispatch_path") or ""),
    )
    return time.perf_counter()


def log_dispatch_node_end(
    state: GraphState,
    *,
    node: str,
    started_at: float,
    patch: dict[str, Any],
) -> None:
    """Конец узла dispatch-cycle: длительность и ключевые счётчики из patch."""
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    result_key = f"{node}_result"
    result = patch.get(result_key)
    status = None
    route = None
    if isinstance(result, dict):
        status = result.get("status")
        route = result.get("route") or result.get("triage_route")
    log.info(
        "verify dispatch_cycle node=%s phase=end duration_ms=%s status=%s route=%s "
        "fix_status=%s",
        node,
        duration_ms,
        status,
        route,
        (patch.get("verify_finding_fix") or {}).get("status")
        if isinstance(patch.get("verify_finding_fix"), dict)
        else patch.get("verify_finding_fix"),
    )
