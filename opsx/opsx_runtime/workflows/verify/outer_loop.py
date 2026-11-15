"""Внешний verify-цикл: gates → triage chain → dispatch batch → финальные gates.

После первого GroupError/RepairOrder/Diagnostic последующие Repair Units из
``remaining_queue`` обрабатываются через ``workflows.verify.outer_batch`` без
повторной группировки до обязательного финального quality gates run.
"""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from workflows.verify.gate_repair.pytest_round_progress import STATE_BASELINE_KEY
from workflows.verify.routing_log import (
    log_group_pass_route,
    log_outer_cycle_begin_route,
)

DEFAULT_VERIFY_OUTER_MAX_ITERATIONS = 20
"""Максимум полных внешних итераций (gates → … → dispatch group) по умолчанию."""

DEFAULT_VERIFY_COHORT_MAX_RESTARTS = 3
"""Перезапуск полного verify-цикла после COHORT_* на финальных gates."""


def resolve_outer_max_iterations(state: GraphState) -> int:
    """Вернуть лимит итераций из state или ``DEFAULT_VERIFY_OUTER_MAX_ITERATIONS``."""
    raw = state.get("verify_outer_max_iterations")
    if isinstance(raw, int) and raw > 0:
        return raw
    return DEFAULT_VERIFY_OUTER_MAX_ITERATIONS


def resolve_cohort_max_restarts(state: GraphState) -> int:
    """Лимит полных перезапусков после COHORT failure."""
    raw = state.get("verify_cohort_max_restarts")
    if isinstance(raw, int) and raw > 0:
        return raw
    return DEFAULT_VERIFY_COHORT_MAX_RESTARTS


def diagnostics_total_count(state: GraphState) -> int | None:
    """``quality_gate_diagnostics.total_count`` если есть."""
    diag = state.get("quality_gate_diagnostics")
    if isinstance(diag, dict):
        total = diag.get("total_count")
        if isinstance(total, int):
            return total
    return None


def quality_gate_actionable_fail(state: GraphState) -> bool:
    """True, если после gates нужен repair chain (есть actionable проблемы)."""
    diag = state.get("quality_gate_diagnostics")
    if not isinstance(diag, dict):
        return True
    total = diag.get("total_count")
    if not isinstance(total, int):
        return True
    if total > 0:
        return True
    if diag.get("input_integrity_status") != "COMPLETE":
        return True
    raw = state.get("quality_gate_raw")
    if isinstance(raw, dict):
        pytest = raw.get("pytest")
        if isinstance(pytest, dict) and pytest.get("exit_code") != 0:
            return True
    if str(state.get("quality_gate_verdict") or "") == "PASS":
        return False
    # Legacy/stale FAIL без in-scope findings (pyright/mypy exit вне scope).
    return False


def is_verify_clean(state: GraphState) -> bool:
    """Нет ошибок для продолжения repair loop после выполненных gates."""
    exec_status = str(state.get("quality_gates_execution_status") or "").strip()
    if not exec_status:
        return False
    if not quality_gate_actionable_fail(state):
        return True
    verdict = str(state.get("quality_gate_verdict") or "")
    if verdict != "PASS":
        return False
    total = diagnostics_total_count(state)
    if total is None:
        return False
    if total == 0:
        return True
    analysis = state.get("analysis_result")
    if isinstance(analysis, dict):
        findings = analysis.get("findings")
        summary = analysis.get("triage_summary")
        if isinstance(findings, list) and not findings:
            if isinstance(summary, dict) and summary.get("findings_total") == 0:
                return True
    return False


def reset_dispatcher_pass_for_next_unit() -> dict[str, Any]:
    """Сброс inner pass для следующей Repair Unit; outcomes/resolved сохраняются."""
    return {
        "dispatcher_pass_initialized": False,
        "dispatcher_pass_queue": [],
        "dispatcher_pass_index": 0,
        "dispatcher_pass_complete": False,
        "dispatcher_cycle_status": "PENDING",
        "dispatcher_current_item": None,
        "dispatcher_route": None,
        "dispatcher_unit_triage_complete": False,
        "dispatch_causal_groups": [],
        "dispatcher_group_index": 0,
        "dispatcher_group_phase": "repair",
        "dispatch_unit_triage_result": None,
        "dispatch_group_verify_result": None,
        "dispatcher_current_group": None,
    }


def reset_dispatcher_pass_patch() -> dict[str, Any]:
    """Сбросить inner dispatcher pass перед новой внешней итерацией (новый diagnostic)."""
    return {
        **reset_dispatcher_pass_for_next_unit(),
        "dispatcher_outcomes": [],
        "dispatcher_resolved_finding_ids": [],
    }


async def verify_begin_outer_cycle_node(state: GraphState) -> dict[str, Any]:
    """Начать внешнюю итерацию: +1 ``verify_outer_iteration``, сброс dispatcher pass."""
    from workflows.verify.outer_batch import reset_outer_batch_patch

    current = int(state.get("verify_outer_iteration") or 0)
    patch: dict[str, Any] = {
        "verify_outer_iteration": current + 1,
        "verify_loop_status": "IN_PROGRESS",
        "verify_loop_terminal": False,
        **reset_dispatcher_pass_patch(),
        **reset_outer_batch_patch(),
        "verify_post_reset_route": None,
        STATE_BASELINE_KEY: None,
    }
    if current == 0:
        patch["verify_workflow_code_changed"] = False
    return patch


async def verify_mark_verify_ok_node(state: GraphState) -> dict[str, Any]:
    """Терминал успеха: чистые gates — ``STATIC_REPAIR_SKIP`` без hop ``static_repair`` agent."""
    import logging

    from workflows.verify.outer_batch import reset_outer_batch_patch
    from workflows.verify.routing_log import explain_is_verify_clean, quality_gate_snapshot

    clean, reason = explain_is_verify_clean(state)
    logging.getLogger("opsx.verify.routing").info(
        "verify gate_repair verify_mark_verify_ok skipped_chain=%s reason=%s snapshot=%s",
        clean,
        reason,
        quality_gate_snapshot(state),
    )
    _ = state
    return {
        "verify_loop_status": "VERIFY_OK",
        "verify_loop_terminal": True,
        "static_repair_status": "SKIPPED_PASS",
        "static_repair_marker": "✅ STATIC_REPAIR_SKIP",
        "static_repair_queue": [],
        "static_repair_issue_count": 0,
        **reset_outer_batch_patch(),
    }


async def verify_after_group_pass_node(state: GraphState) -> dict[str, Any]:
    """После dispatch_cycle: следующий unit batch, финальные gates, full cycle или terminal."""
    from workflows.verify.outer_batch import decide_outer_route_after_unit

    decision, patch = decide_outer_route_after_unit(state)
    patch["verify_outer_route_decision"] = decision
    return patch


def route_after_outer_cycle_begin(state: GraphState) -> str:
    """Legacy alias: маршрут после ``quality_gates`` (``route_after_quality_gates``)."""
    return route_after_quality_gates(state)


def route_after_quality_gates(state: GraphState) -> str:
    """После ``quality_gates``: PASS → skip terminal; batch final FAIL → group_error; иначе chain."""
    if state.get("verify_outer_batch_final_pending"):
        if is_verify_clean(state):
            target = "verify_mark_verify_ok"
        else:
            target = "verify_reset_repair_batch"
        log_outer_cycle_begin_route(state, target=target)
        return target
    if is_verify_clean(state):
        target = "verify_mark_verify_ok"
    else:
        target = "group_error"
    log_outer_cycle_begin_route(state, target=target)
    return target


def route_after_reset_repair_batch(state: GraphState) -> str:
    """После финальных gates: terminal, полный перезапуск или продолжение group_error."""
    from workflows.stop_errors import VerifyStageStop
    from workflows.verify.routing_log import log_reset_repair_batch_route

    if state.get("verify_loop_terminal"):
        raise VerifyStageStop(
            "verify loop terminal after reset repair batch",
            code="VERIFY_LOOP_TERMINAL",
            stage="gate_repair",
            evidence=str(state.get("verify_loop_status") or ""),
        )
    elif str(state.get("verify_post_reset_route") or "") == "full_restart":
        target = "verify_begin_outer_cycle"
    else:
        target = "group_error"
    log_reset_repair_batch_route(state, target=str(target))
    return str(target)


def route_after_group_pass(state: GraphState) -> str:
    """После dispatch group: next unit, final gates, full cycle, terminal или cap."""
    from workflows.stop_errors import VerifyStageStop

    decision = str(state.get("verify_outer_route_decision") or "")
    if state.get("verify_loop_terminal"):
        if is_verify_clean(state):
            target = "verify_mark_verify_ok"
        elif str(state.get("verify_loop_status") or "") == "PAUSE":
            raise VerifyStageStop(
                "verify paused",
                code="VERIFY_PAUSED",
                stage="gate_repair",
                evidence=str(state.get("owner_attention") or ""),
            )
        else:
            raise VerifyStageStop(
                "verify loop terminal not clean",
                code="VERIFY_LOOP_TERMINAL",
                stage="gate_repair",
                evidence=str(state.get("verify_loop_status") or ""),
            )
    elif decision in ("next_unit", "harness_recovery"):
        target = "dispatch_cycle"
    elif decision == "final_gates":
        target = "quality_gates"
    elif decision == "batch_recheck_gates":
        target = "quality_gates"
    elif decision == "full_cycle":
        target = "verify_begin_outer_cycle"
    else:
        target = "verify_begin_outer_cycle"
    log_group_pass_route(state, target=str(target))
    return target
