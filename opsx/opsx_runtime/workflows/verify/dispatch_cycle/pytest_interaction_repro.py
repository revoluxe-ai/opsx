"""Ограниченное воспроизведение порядка/конкуренции для test_interaction (тот же argv builder)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from workflows.verify.dispatch_cycle.single_repro_probe_key import batch_workers_from_state
from workflows.verify.gate_repair.pytest_gate_timeout import QUALITY_GATE_PER_TEST_SECONDS
from workflows.verify.gate_repair.pytest_subprocess_streaming import run_pytest_gate_with_progress
from workflows.verify.gate_repair.runner import _resolve_poetry
from workflows.verify.pytest_workflow_argv import build_workflow_pytest_argv

InteractionReproKind = Literal["ordered_sequence", "concurrent"]
_MAX_SEQUENCE_NODES = 8
_MAX_CONCURRENT_RUNS = 1


def run_bounded_interaction_repro(
    repo: Path,
    *,
    node_ids: list[str],
    kind: InteractionReproKind,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Структурированное evidence: ordered sequence или одно ограниченное -n (если batch parallel)."""
    if not node_ids:
        return {"status": "SKIPPED", "reason": "empty_node_list"}
    prefix = _resolve_poetry(repo)
    workers = batch_workers_from_state(state)
    bounded_nodes = list(dict.fromkeys(node_ids))[:_MAX_SEQUENCE_NODES]
    attempts: list[dict[str, Any]] = []

    if kind == "concurrent":
        if workers <= 1:
            return {
                "status": "UNCLEAR",
                "reason": "batch_not_parallel",
                "note": "sequential_probe_does_not_prove_race",
            }
        argv = build_workflow_pytest_argv(
            repo,
            paths=bounded_nodes,
            poetry_prefix=prefix,
            quiet=False,
        )
        insert_at = argv.index("-m")
        argv = [
            *argv[:insert_at],
            "-n",
            str(workers),
            *argv[insert_at:],
        ]
        started = time.monotonic()
        gate, log = run_pytest_gate_with_progress(
            argv,
            cwd=repo,
            timeout_seconds=None,
            scope="interaction-concurrent",
            test_count=len(bounded_nodes),
            per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        )
        attempts.append(
            {
                "kind": "concurrent",
                "workers": workers,
                "exit_code": int(gate.exit_code or 0),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "log_tail": log[-2000:],
            }
        )
        reproduced = int(gate.exit_code or 0) != 0
        return {
            "status": "REPRODUCED" if reproduced else "UNCLEAR",
            "attempts": attempts,
            "reason": None if reproduced else "concurrency_not_reproduced",
        }

    for node in bounded_nodes:
        argv = build_workflow_pytest_argv(
            repo,
            paths=[node],
            poetry_prefix=prefix,
            quiet=False,
        )
        started = time.monotonic()
        gate, log = run_pytest_gate_with_progress(
            argv,
            cwd=repo,
            timeout_seconds=None,
            scope=node,
            test_count=1,
            per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        )
        attempts.append(
            {
                "kind": "ordered",
                "node_id": node,
                "exit_code": int(gate.exit_code or 0),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "log_tail": log[-1200:],
            }
        )
        if len(attempts) >= _MAX_SEQUENCE_NODES:
            break
    any_fail = any(item.get("exit_code") for item in attempts)
    return {
        "status": "SEQUENCE_DONE",
        "attempts": attempts,
        "sequence_not_race_proof": True,
        "any_fail_in_sequence": any_fail,
    }
