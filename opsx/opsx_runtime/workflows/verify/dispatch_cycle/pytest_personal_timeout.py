"""Пробный прогон одного node id с потолком 300 с и запись персонального маркера."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.repro_cohort import pytest_node_id
from agents.verify.test_duration.timeout_policy import (
    PERSONAL_TIMEOUT_CEILING_SECONDS,
    duration_probe_key,
    measured_personal_timeout_seconds,
    write_pytest_timeout_marker,
)
from workflows.verify.gate_repair.pytest_gate_timeout import QUALITY_GATE_PER_TEST_SECONDS
from workflows.verify.gate_repair.pytest_opsx_gate_progress import (
    PROBE_CEILING_ENV,
    PROBE_NODE_ENV,
)
from workflows.verify.gate_repair.pytest_subprocess_streaming import run_pytest_gate_with_progress
from workflows.verify.gate_repair.runner import _resolve_poetry
from workflows.verify.pytest_workflow_argv import (
    apply_workflow_pytest_timeout,
    build_workflow_pytest_argv,
)


def run_personal_timeout_probe(state: GraphState) -> dict[str, Any]:
    """Один node до 300 с. Успех пишет маркер; упор в потолок — UNCLEAR, не новый лимит."""
    item = state.get("dispatcher_current_item")
    observation = ""
    if isinstance(item, dict):
        observation = str(item.get("observation") or "")
    node_id = pytest_node_id(observation)
    if not node_id or node_id == "UNKNOWN_NODE":
        return {
            "duration_probe_exhausted": True,
            "personal_timeout_marker_seconds": None,
            "personal_timeout_probe": {"status": "NO_NODE"},
        }

    probe_key = duration_probe_key(node_id, observation)
    prior_keys = state.get("duration_probe_keys")
    completed: list[str] = list(prior_keys) if isinstance(prior_keys, list) else []
    if probe_key in completed:
        prior_probe = state.get("personal_timeout_probe")
        if isinstance(prior_probe, dict):
            return {
                "duration_probe_skipped": True,
                "personal_timeout_probe": {**prior_probe, "status": "SKIPPED_ALREADY_RUN"},
            }
        return {
            "duration_probe_skipped": True,
            "personal_timeout_probe": {
                "status": "SKIPPED_ALREADY_RUN",
                "node_id": node_id,
            },
        }

    repo = Path(str(state["repository_path"]))
    prefix = _resolve_poetry(repo)
    argv = apply_workflow_pytest_timeout(
        build_workflow_pytest_argv(
            repo,
            paths=[node_id],
            poetry_prefix=prefix,
            quiet=False,
        ),
        PERSONAL_TIMEOUT_CEILING_SECONDS,
    )
    started = time.monotonic()
    probe_timeout = float(PERSONAL_TIMEOUT_CEILING_SECONDS) + 20.0
    probe_env = {
        PROBE_NODE_ENV: node_id,
        PROBE_CEILING_ENV: str(PERSONAL_TIMEOUT_CEILING_SECONDS),
    }
    gate, log = run_pytest_gate_with_progress(
        argv,
        cwd=repo,
        timeout_seconds=probe_timeout,
        scope=node_id,
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        call_phase_limit_seconds=float(PERSONAL_TIMEOUT_CEILING_SECONDS),
        extra_env=probe_env,
    )
    elapsed = time.monotonic() - started
    hit_ceiling = (
        "PYTEST_STALL" in log
        or "PYTEST_SUITE_TIMEOUT" in log
        or "Timeout" in log
        or gate.error is not None
    )
    base_probe_patch = {"duration_probe_keys": [*completed, probe_key]}
    if gate.exit_code == 0 and gate.error is None:
        seconds = measured_personal_timeout_seconds(elapsed)
        written = write_pytest_timeout_marker(repo, node_id, seconds)
        return {
            **base_probe_patch,
            "duration_probe_exhausted": False,
            "personal_timeout_marker_seconds": seconds if written else None,
            "personal_timeout_probe": {
                "status": "PASS",
                "node_id": node_id,
                "elapsed_s": elapsed,
                "marker_seconds": seconds,
            },
            "test_duration_result": {
                "schema_version": "1.0",
                "agent": "test_duration",
                "finding_id": str(item.get("finding_id") if isinstance(item, dict) else ""),
                "classification": "PERSONAL_TIMEOUT",
                "evidence_refs": {},
                "rationale": (
                    f"personal timeout probe exit_code=0 elapsed_s={elapsed:.2f} marker={seconds}s"
                ),
            },
        }
    return {
        **base_probe_patch,
        "duration_probe_exhausted": True,
        "personal_timeout_marker_seconds": None,
        "personal_timeout_probe": {
            "status": "UNCLEAR" if hit_ceiling else "FAIL",
            "node_id": node_id,
            "elapsed_s": elapsed,
            "exit_code": gate.exit_code,
        },
        "test_duration_result": {
            "schema_version": "1.0",
            "agent": "test_duration",
            "finding_id": str(item.get("finding_id") if isinstance(item, dict) else ""),
            "classification": "UNCLEAR",
            "evidence_refs": {},
            "rationale": (
                "personal timeout probe stopped without proof of hang "
                f"(exit_code={gate.exit_code}, hit_ceiling={hit_ceiling})"
            ),
        },
    }
