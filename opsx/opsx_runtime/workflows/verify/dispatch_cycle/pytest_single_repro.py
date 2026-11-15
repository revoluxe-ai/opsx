"""Детерминированный одиночный pytest-probe до triage/repair (workflow argv, без xdist)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Literal

from agents.state import GraphState
from agents.verify.dispatch_unit.repro_cohort import pytest_node_id
from agents.verify.test_duration.timeout_policy import is_pytest_duration_text
from workflows.verify.dispatch_cycle.single_repro_probe_key import build_single_repro_probe_key
from workflows.verify.gate_repair.pytest_gate_timeout import QUALITY_GATE_PER_TEST_SECONDS
from workflows.verify.gate_repair.pytest_subprocess_streaming import run_pytest_gate_with_progress
from workflows.verify.gate_repair.runner import _resolve_poetry
from workflows.verify.gate_repair.pytest_opsx_gate_progress_parse import (
    iter_opsx_gate_segments,
)
from workflows.verify.pytest_workflow_argv import build_workflow_pytest_argv

SingleReproVerdict = Literal[
    "PASS",
    "SAME_FAIL",
    "DIFFERENT_FAIL",
    "NOT_PASS",
    "NO_NODE",
    "SKIPPED_KEY",
]

_FAIL_LINE_RE = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<node>[^\s]+?\.py(?:::[^\s]+)*)\s+-\s+(?P<detail>.+)$"
)


def failure_signature_from_observation(observation: str) -> str:
    """Нормализованная сигнатура отказа из gate observation."""
    raw = observation.strip()
    if " - " in raw:
        _, tail = raw.split(" - ", 1)
        return tail.strip()[:500]
    return raw[:500]


def failure_signature_from_log(log: str, node_id: str) -> str:
    """Сигнатура отказа из stdout/stderr одиночного прогона."""
    for line in reversed(log.splitlines()):
        match = _FAIL_LINE_RE.match(line.strip())
        if match and match.group("node") == node_id:
            return match.group("detail").strip()[:500]
        if node_id in line and ("FAILED" in line or "ERROR" in line):
            if " - " in line:
                return line.split(" - ", 1)[1].strip()[:500]
    for token in ("PYTEST_STALL", "PYTEST_SUITE_TIMEOUT", "Timeout", "CollectionError"):
        if token in log:
            return token
    return log.strip()[-500:]


def failure_signatures_match(left: str, right: str) -> bool:
    """Сравнение сигнатур: точное совпадение или общий тип исключения."""
    if not left or not right:
        return False
    if left == right:
        return True
    left_head = left.split(":", 1)[0].strip()
    right_head = right.split(":", 1)[0].strip()
    return bool(left_head and left_head == right_head)


def parse_node_call_outcome(log: str, node_id: str) -> str | None:
    """Исход call-фазы выбранного node по OPSX_GATE_PROGRESS."""
    target = node_id.replace("\\", "/")
    outcome: str | None = None
    for line in log.splitlines():
        for gate in iter_opsx_gate_segments(line):
            node = str(gate.get("node") or "").replace("\\", "/")
            if node != target:
                continue
            if gate.get("when") != "call" or gate.get("event") != "report":
                continue
            outcome = str(gate.get("outcome") or "").lower()
    return outcome


def classify_single_repro(
    *,
    node_id: str,
    observation: str,
    exit_code: int,
    log: str,
    gate_error: str | None,
) -> SingleReproVerdict:
    """Классифицировать исход probe: PASS только при реальном passed call."""
    call_outcome = parse_node_call_outcome(log, node_id)
    if "no tests ran" in log.lower() or "errors during collection" in log.lower():
        return "NOT_PASS"
    if gate_error or "PYTEST_SUITE_TIMEOUT" in log or "PYTEST_STALL" in log:
        return "NOT_PASS"
    if call_outcome in {"skipped", "xfail", "xpass"}:
        return "NOT_PASS"
    if exit_code == 0 and call_outcome == "passed":
        return "PASS"
    if call_outcome not in {None, "passed"} and exit_code == 0:
        return "NOT_PASS"
    obs_sig = failure_signature_from_observation(observation)
    probe_sig = failure_signature_from_log(log, node_id)
    if failure_signatures_match(obs_sig, probe_sig):
        return "SAME_FAIL"
    if exit_code != 0 or call_outcome in {"failed", "error"}:
        if probe_sig and not failure_signatures_match(obs_sig, probe_sig):
            return "DIFFERENT_FAIL"
        return "SAME_FAIL"
    return "NOT_PASS"


def run_pytest_single_repro_node(state: GraphState) -> dict[str, Any]:
    """Один node id через workflow gates argv; дедуп по causal probe key."""
    item = state.get("dispatcher_current_item")
    if not isinstance(item, dict):
        raise ValueError("dispatcher_current_item required for pytest_single_repro")
    observation = str(item.get("observation") or "")
    node_id = pytest_node_id(observation) or ""
    if not node_id or node_id == "UNKNOWN_NODE":
        return {
            "pytest_single_repro_result": {
                "status": "NO_NODE",
                "node_id": node_id or None,
                "observation": observation[:300],
            },
            "verify_dispatch_path": "test",
        }

    repo = Path(str(state["repository_path"]))
    probe_key = build_single_repro_probe_key(
        repo,
        node_id=node_id,
        observation=observation,
        state=state,
    )
    prior = state.get("single_repro_probe_keys")
    completed = list(prior) if isinstance(prior, list) else []
    if probe_key in completed:
        cached = state.get("pytest_single_repro_result")
        if isinstance(cached, dict):
            return {
                "pytest_single_repro_skipped": True,
                "pytest_single_repro_result": {**cached, "status": "SKIPPED_KEY"},
            }
        return {
            "pytest_single_repro_skipped": True,
            "pytest_single_repro_result": {
                "status": "SKIPPED_KEY",
                "probe_key": probe_key,
                "node_id": node_id,
            },
        }

    prefix = _resolve_poetry(repo)
    argv = build_workflow_pytest_argv(
        repo,
        paths=[node_id],
        poetry_prefix=prefix,
        quiet=False,
    )
    started = time.monotonic()
    gate, log = run_pytest_gate_with_progress(
        argv,
        cwd=repo,
        timeout_seconds=None,
        scope=node_id,
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    verdict = classify_single_repro(
        node_id=node_id,
        observation=observation,
        exit_code=int(gate.exit_code or 0),
        log=log,
        gate_error=gate.error,
    )
    batch_note = (
        "batch_conditions_not_identical: suite order/workers may differ from single-node run"
    )
    return {
        "verify_dispatch_path": "test",
        "single_repro_probe_keys": [*completed, probe_key],
        "pytest_single_repro_result": {
            "status": verdict,
            "node_id": node_id,
            "probe_key": probe_key,
            "exit_code": int(gate.exit_code or 0),
            "elapsed_ms": elapsed_ms,
            "command": argv,
            "log_tail": log[-3500:],
            "gate_error": gate.error,
            "observation_failure_signature": failure_signature_from_observation(observation),
            "probe_failure_signature": failure_signature_from_log(log, node_id),
            "batch_conditions_note": batch_note,
            "duration_routing": is_pytest_duration_text(observation),
        },
    }
