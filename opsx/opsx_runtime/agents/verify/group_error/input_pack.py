"""Сбор verification package для Group Error Agent из ``GraphState``."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.verify.gate_repair.input_integrity import is_pytest_structured_gate_evidence


def _git_revision(repo: Path) -> str | None:
    try:
        proc = run_subprocess_utf8(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _default_criteria() -> list[dict[str, Any]]:
    """Machine criteria для static gates (не менять смысл — только handoff)."""
    return [
        {
            "criterion_id": "CRIT-GATE-PYTEST",
            "description": "pytest gate (opsx_runtime verify tests) completes without failures",
            "mandatory": True,
            "related_checks": ["pytest"],
        },
        {
            "criterion_id": "CRIT-GATE-PYRIGHT-STRICT",
            "description": "pyright strict gate completes without type errors",
            "mandatory": True,
            "related_checks": ["pyright"],
        },
        {
            "criterion_id": "CRIT-GATE-MYPY",
            "description": "mypy gate completes without type errors",
            "mandatory": True,
            "related_checks": ["mypy"],
        },
    ]


def build_verification_package(
    *,
    change_id: str,
    repository_path: str,
    state: dict[str, Any],
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Нормализованный вход triage: identity, diagnostics, raw gate evidence."""
    repo = Path(repository_path)
    from agents.verify.group_error.gate_only_triage import findings_from_gate_state

    gate_findings = findings_from_gate_state(state)
    raw = state.get("quality_gate_raw") or {}
    diagnostics = state.get("quality_gate_diagnostics") or {}
    finished_at = datetime.now(timezone.utc).isoformat()

    check_results: list[dict[str, Any]] = []
    integrity_issues: list[str] = []
    for check_id in ("pytest", "pyright", "mypy"):
        block = raw.get(check_id) if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            check_results.append(
                {
                    "check_id": check_id,
                    "name": check_id,
                    "status": "NOT_RUN",
                    "evidence_ref": f"quality_gate_raw.{check_id}",
                },
            )
            integrity_issues.append(f"quality_gate_raw.{check_id} missing")
            continue
        raw_error = block.get("error")
        if raw_error:
            structured_pytest_evidence = (
                check_id == "pytest"
                and is_pytest_structured_gate_evidence(str(raw_error))
            )
            if not structured_pytest_evidence:
                integrity_issues.append(
                    f"quality_gate_raw.{check_id}.error={raw_error}",
                )
        check_results.append(
            {
                "check_id": check_id,
                "name": check_id,
                "command": block.get("command"),
                "exit_code": block.get("exit_code"),
                "duration_ms": block.get("duration_ms"),
                "stdout_tail": block.get("stdout_tail"),
                "stderr_tail": block.get("stderr_tail"),
                "error": block.get("error"),
                "parsed_issues_ref": f"quality_gate_diagnostics.{check_id}",
                "parsed_issues": diagnostics.get(check_id)
                if isinstance(diagnostics, dict)
                else [],
                "evidence_ref": f"quality_gate_raw.{check_id}",
                "status": "COMPLETED",
            },
        )

    qg_exec = str(state.get("quality_gates_execution_status") or "").strip()
    if not qg_exec:
        integrity_issues.append("quality_gates_execution_status missing")

    exec_integrity_status: str = "COMPLETE"
    if integrity_issues:
        exec_integrity_status = "INVALID"

    return {
        "verification_identity": {
            "change_id": change_id,
            "execution_id": execution_id,
            "revision": _git_revision(repo),
            "workflow_revision": "verify-static-repair-subgraph-v1",
            "finished_at": finished_at,
        },
        "impl_verify": {
            "verdict": state.get("verdict"),
            "marker": state.get("marker"),
        },
        "quality_gates": {
            "verdict": state.get("quality_gate_verdict"),
            "marker": state.get("quality_gate_marker"),
            "execution_status": state.get("quality_gates_execution_status"),
        },
        "criteria": _default_criteria(),
        "check_results": check_results,
        "diagnostics": diagnostics,
        "execution_integrity": {
            "status": exec_integrity_status,
            "issues": integrity_issues,
        },
        "findings": [
            f.model_dump(mode="json") if hasattr(f, "model_dump") else f
            for f in gate_findings
        ],
        "limitations": [
            "Log tails in check_results are truncated (~3.5k chars per stream) for evidence only.",
            "Machine diagnostics lists are parsed from full mypy stdout before tail truncation.",
            "Only pytest/pyright/mypy gates are in this package.",
            (
                "input_integrity_status in diagnostics is computed deterministically from mypy "
                "completeness; LLM must not override it."
            ),
        ],
    }


def package_json(package: dict[str, Any]) -> str:
    """JSON для prompt (bounded)."""
    return json.dumps(package, ensure_ascii=False, indent=2)
