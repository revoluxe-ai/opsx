"""Conditional edges static/test path внутри dispatch cycle."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.test_duration.timeout_policy import (
    duration_probe_key,
    is_pytest_duration_text,
)
from agents.verify.dispatch_unit.repro_cohort import pytest_node_id


def _single_repro_payload(state: GraphState) -> dict:
    raw = state.get("pytest_single_repro_result")
    return raw if isinstance(raw, dict) else {}


def pytest_single_repro_route_edge(state: GraphState) -> str:
    """Маршрут после одиночного probe: triage, duration, interaction."""
    payload = _single_repro_payload(state)
    status = str(payload.get("status") or "")
    if status in {"NO_NODE", "SKIPPED_KEY"}:
        if payload.get("duration_routing") or is_pytest_duration_text(
            str((state.get("dispatcher_current_item") or {}).get("observation") or "")
            if isinstance(state.get("dispatcher_current_item"), dict)
            else "",
        ):
            return "test_duration"
        return "test_triage"
    if status == "PASS":
        return "test_interaction"
    if status in {"SAME_FAIL", "NOT_PASS"}:
        if payload.get("duration_routing"):
            return "test_duration"
        return "test_triage"
    return "test_triage"


def interaction_post_probe_route_edge(state: GraphState) -> str:
    """Куда направить repair_owner после test_interaction."""
    result = state.get("test_interaction_result")
    if not isinstance(result, dict):
        return "test_finding_finalize"
    owner = str(result.get("repair_owner") or "UNCLEAR")
    if owner == "logic_bug_repair":
        return "logic_bug_repair"
    if owner == "test_repair":
        return "test_repair"
    return "test_finding_finalize"


def _test_repair_requires_batch_recheck(state: GraphState) -> bool:
    """FIXED test-path repair завершается полной пачкой, не finalize."""
    if _is_causal_group_repair(state):
        return False
    if str(state.get("verify_dispatch_path") or "") != "test":
        return False
    fix = state.get("verify_finding_fix")
    if not isinstance(fix, dict):
        return False
    return str(fix.get("status") or "") == "FIXED"


def _commit_allows_batch_recheck(state: GraphState) -> bool:
    status = str(state.get("commit_status") or "")
    if status in {"COMMITTED", "SKIPPED_ALREADY_COMMITTED"}:
        return True
    outcome = state.get("verify_finding_commit_outcome")
    if isinstance(outcome, dict) and outcome.get("error"):
        return False
    return status == "SKIPPED_ALREADY_COMMITTED"


def static_triage_route_edge(state: GraphState) -> str:
    """После static triage: LOGIC → bug fix, TYPING → typing_repair, иначе finalize."""
    triage = state.get("static_triage_result")
    if not isinstance(triage, dict):
        return "static_finding_finalize"
    classification = triage.get("classification")
    if classification == "LOGIC":
        return "logic_bug_repair"
    if classification == "TYPING":
        return "typing_repair"
    return "static_finding_finalize"


def duration_route_edge(state: GraphState) -> str:
    """После агента длительности: зависание кода, зависание теста или пробный таймаут."""
    result = state.get("test_duration_result")
    if not isinstance(result, dict):
        return "test_finding_finalize"
    classification = result.get("classification")
    if classification == "HANG_PRODUCT":
        return "logic_bug_repair"
    if classification == "HANG_TEST":
        return "test_repair"
    if classification == "PERSONAL_TIMEOUT" and not state.get("duration_probe_exhausted"):
        return "pytest_personal_timeout"
    if classification == "PERSONAL_TIMEOUT":
        return "test_finding_finalize"
    if classification == "UNCLEAR" and _should_run_duration_probe(state):
        return "pytest_personal_timeout"
    return "test_finding_finalize"


def personal_timeout_probe_route_edge(state: GraphState) -> str:
    """Успешный пробный прогон закрывает finding; упор в 300 с возвращает агенту длительности."""
    if state.get("personal_timeout_marker_seconds"):
        if _is_causal_group_repair(state):
            return "group_verify"
        return "test_finding_finalize"
    probe = state.get("personal_timeout_probe")
    if isinstance(probe, dict) and probe.get("status") in {"HANG", "UNCLEAR", "FAIL"}:
        return "test_finding_finalize"
    return "test_repair"


def _should_run_duration_probe(state: GraphState) -> bool:
    """Один pytest probe на ключ node+observation, пока не exhausted."""
    if state.get("duration_probe_exhausted"):
        return False
    item = state.get("dispatcher_current_item")
    if not isinstance(item, dict):
        return False
    observation = str(item.get("observation") or "")
    if not is_pytest_duration_text(observation):
        return False
    node_id = pytest_node_id(observation) or ""
    if not node_id:
        return False
    key = duration_probe_key(node_id, observation)
    prior_keys = state.get("duration_probe_keys")
    if isinstance(prior_keys, list) and key in prior_keys:
        return False
    return True


def pytest_finding_triage_route_edge(state: GraphState) -> str:
    """После test triage: LOGIC → bug fix, TEST → execute pre-repair или test_repair."""
    triage = state.get("test_triage_result")
    if not isinstance(triage, dict):
        return "test_finding_finalize"
    classification = triage.get("classification")
    if classification == "LOGIC":
        return "logic_bug_repair"
    if classification == "TEST":
        from agents.verify.dispatch_mode import resolve_verify_dispatch_mode

        if resolve_verify_dispatch_mode(state) == "per_finding":
            return "pytest_execute_pre_repair"
        return "test_repair"
    return "test_finding_finalize"


def _is_causal_group_repair(state: GraphState) -> bool:
    item = state.get("dispatcher_current_item")
    return isinstance(item, dict) and item.get("item_kind") == "causal_group"


def finding_post_repair_route_edge(state: GraphState) -> str:
    """После repair hop: commit если FIXED с files_changed, иначе finalize по path."""
    if _is_causal_group_repair(state):
        fix = state.get("verify_finding_fix")
        if isinstance(fix, dict) and fix.get("status") == "FIXED":
            files = fix.get("files_changed")
            if isinstance(files, list) and files:
                return "finding_commit"
        return "group_verify"
    fix = state.get("verify_finding_fix")
    if isinstance(fix, dict) and fix.get("status") == "FIXED":
        files = fix.get("files_changed")
        if isinstance(files, list) and files:
            return "finding_commit"
        if _test_repair_requires_batch_recheck(state):
            return "batch_recheck_handoff"
    path = state.get("verify_dispatch_path")
    if path == "test":
        return "test_finding_finalize"
    return "static_finding_finalize"


def post_commit_route_edge(state: GraphState) -> str:
    """После commit вернуться в finalize соответствующего workflow."""
    if _is_causal_group_repair(state):
        return "group_verify"
    if state.get("verify_dispatch_path") == "test":
        if _commit_allows_batch_recheck(state):
            return "batch_recheck_handoff"
        return "test_finding_finalize"
    return "static_finding_finalize"
