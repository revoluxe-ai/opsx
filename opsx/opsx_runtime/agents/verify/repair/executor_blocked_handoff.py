"""Handoff при BLOCKED IMPLEMENTATION_EXECUTOR без shell/pytest (product defect остаётся)."""

from __future__ import annotations

import re
from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.repro_cohort import pytest_node_id
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)

_NO_SHELL_MARKERS = (
    "no shell",
    "no shell/runtime",
    "could not be executed",
    "pytest could not",
    "execution tools unavailable",
)

_HOP_RE = re.compile(r"\b\d{3}-implementation-executor-[0-9a-f]+\b", re.IGNORECASE)


def _blocked_executor_text(fix: dict[str, Any]) -> str:
    parts = [str(fix.get("summary") or ""), str(fix.get("notes") or "")]
    return " ".join(parts).strip()


def is_implementation_executor_blocked_no_shell(fix: dict[str, Any] | None) -> bool:
    """True для честного BLOCKED fix-hop без повторного pytest."""
    if not isinstance(fix, dict):
        return False
    if str(fix.get("status") or "").upper() != "BLOCKED":
        return False
    lowered = _blocked_executor_text(fix).lower()
    return any(marker in lowered for marker in _NO_SHELL_MARKERS)


def _named_test_from_state(state: GraphState) -> str | None:
    item = state.get("dispatcher_current_item")
    if isinstance(item, dict):
        for key in ("observation", "expected_failure", "actual_failure"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                node = pytest_node_id(raw)
                if node:
                    return node
    diag = state.get("quality_gate_diagnostics")
    if isinstance(diag, dict):
        pytest_raw = diag.get("pytest")
        if isinstance(pytest_raw, list):
            for obs in pytest_raw:
                node = pytest_node_id(str(obs))
                if node:
                    return node
    return None


def _hop_label_from_state(state: GraphState, fix: dict[str, Any]) -> str:
    for source in (_blocked_executor_text(fix), str(state.get("stream_log") or "")):
        match = _HOP_RE.search(source)
        if match:
            return match.group(0)
    snapshot = state.get("verify_static_repair_state_snapshot")
    if isinstance(snapshot, dict):
        mission = str(snapshot.get("mission_id") or "").strip()
        if mission:
            return mission
    return "implementation-executor"


def build_executor_blocked_product_handoff_owner(
    *,
    hop: str,
    named_test: str,
    reason: str,
) -> str:
    """Текст owner_attention для VERIFY_PAUSED без ложного incomplete-diagnostics."""
    return (
        "REPAIR_PRODUCT_HANDOFF_BLOCKED: failed quality gate; "
        f"implementation_executor hop={hop} status=BLOCKED reason={reason}; "
        f"named_test={named_test}; quality_gate_verdict remains FAIL; "
        "gates already ran pytest — executor must not rerun shell; "
        "Owner confirms product fix locally before next verify"
    )


def maybe_executor_blocked_product_handoff_patch(state: GraphState) -> dict[str, Any]:
    """Вернуть patch с явным product handoff или пустой dict."""
    fix = state.get("verify_finding_fix")
    if not is_implementation_executor_blocked_no_shell(
        fix if isinstance(fix, dict) else None,
    ):
        return {}

    diag_raw = state.get("quality_gate_diagnostics")
    if not isinstance(diag_raw, dict):
        return {}
    integrity = input_integrity_from_gate_diagnostics(diag_raw)
    if integrity.status != "COMPLETE":
        return {}

    named_test = _named_test_from_state(state)
    if not named_test:
        return {}

    hop = _hop_label_from_state(state, fix if isinstance(fix, dict) else {})
    reason = "no_shell_no_pytest_rerun"
    owner = build_executor_blocked_product_handoff_owner(
        hop=hop,
        named_test=named_test,
        reason=reason,
    )
    entry = {
        "hop": hop,
        "named_test": named_test,
        "reason": reason,
        "status": "BLOCKED",
    }
    diag = dict(diag_raw)
    blocked_raw = diag.get("repair_execution_blocked")
    blocked: list[dict[str, str]] = []
    if isinstance(blocked_raw, list):
        for item in blocked_raw:
            if isinstance(item, dict):
                blocked.append({str(k): str(v) for k, v in item.items()})
    if entry not in blocked:
        blocked.append(entry)
    diag["repair_execution_blocked"] = blocked

    return {
        "quality_gate_diagnostics": diag,
        "owner_attention": owner,
        "verify_executor_blocked_handoff": entry,
    }


def maybe_executor_blocked_terminal_pause_patch(
    state: GraphState,
) -> dict[str, Any] | None:
    """Если integrity COMPLETE и executor BLOCKED — terminal PAUSE с product handoff."""
    handoff = maybe_executor_blocked_product_handoff_patch(state)
    if not handoff:
        return None
    from workflows.verify.outer_batch import reset_outer_batch_patch

    return {
        "verify_loop_status": "PAUSE",
        "verify_loop_terminal": True,
        **handoff,
        **reset_outer_batch_patch(),
    }
