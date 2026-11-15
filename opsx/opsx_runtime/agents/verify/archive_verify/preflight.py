"""Детерминированный preflight для PHASE_ARCHIVE (без LLM-вердикта)."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from agents.verify.archive_verify.receipt_rollup import collect_verify_receipt_rollup
from agents.verify.implementation_track import count_implementation_track
from workflows.implementation.node_subprocess import run_subprocess_utf8

from agents.verify.impl_verify.preflight import (
    REPO_ROOT,
    change_root,
    load_change_artifact_bundle,
    preflight_implies_gaps,
    run_config_contract_check,
    run_split_apply_completion,
)

_LOGGER = logging.getLogger(__name__)

_PA_REQUIRED = re.compile(
    r"PRODUCT_ACCEPTANCE_REQUIRED[^\n]*\|\s*\*\*(TRUE|FALSE|NOT_REQUIRED|NOT_CONFIRMED)\*\*",
    re.IGNORECASE,
)


def _count_tasks_markers(tasks_text: str | None) -> dict[str, int]:
    """Legacy alias: IMPLEMENTATION_TRACK only (``TASK_GENERATION`` §0)."""
    stats = count_implementation_track(tasks_text)
    return {
        "total": int(stats["total"]),
        "completed": int(stats["completed"]),
        "remaining": int(stats["remaining"]),
    }


def load_tasks_progress(change_id: str, *, repo_root: Path = REPO_ROOT) -> dict[str, int | str]:
    """Счётчики IMPLEMENTATION_TRACK из ``tasks.md`` change."""
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    tasks_blob = (bundle.get("artifacts") or {}).get("tasks.md")
    return count_implementation_track(tasks_blob if isinstance(tasks_blob, str) else None)


def evaluate_product_acceptance(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Прочитать evidence PA без вызова LLM (ядро archive gate)."""
    root = change_root(change_id, repo_root=repo_root)
    candidates = [
        resource_path(root, "evidence", "product-acceptance-applicability.md"),
        resource_path(root, "evidence", "product-acceptance.md"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        required_match = _PA_REQUIRED.search(text)
        required_token = (
            required_match.group(1).upper() if required_match else "NOT_VERIFIED"
        )
        if "NOT_PASS" in text or "product_acceptance_not_pass" in text.lower():
            return {
                "status": "FAIL",
                "source": str(path.relative_to(repo_root)),
                "required": required_token,
            }
        if "BLOCKED" in text and "PRODUCT_ACCEPTANCE" in text:
            return {
                "status": "BLOCKED",
                "source": str(path.relative_to(repo_root)),
                "required": required_token,
            }
        if "PASS" in text and "PRODUCT_ACCEPTANCE" in text:
            return {
                "status": "PASS",
                "source": str(path.relative_to(repo_root)),
                "required": required_token,
            }
        if required_token in ("FALSE", "NOT_REQUIRED"):
            return {
                "status": "NOT_APPLICABLE",
                "source": str(path.relative_to(repo_root)),
                "required": required_token,
            }
        return {
            "status": "NOT_VERIFIED",
            "source": str(path.relative_to(repo_root)),
            "required": required_token,
        }
    return {"status": "NOT_VERIFIED", "source": None, "required": "UNKNOWN"}


def run_channel_archive_gate(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    base: str = "HEAD~1",
    head: str = "HEAD",
) -> dict[str, Any]:
    """Вызвать Channel Impact archive-gate (read-only subprocess)."""
    script = resource_path(repo_root, ".cursor", "pipeline", "channel-impact", "channel_impact_guard.py")
    if not script.is_file():
        return {"status": "SKIP", "reason": "channel_impact_guard_missing"}
    proc = run_subprocess_utf8(
        [
            sys.executable,
            str(script),
            "archive-gate",
            "--change",
            change_id,
            "--base",
            base,
            "--head",
            head,
            "--root",
            str(repo_root),
        ],
        cwd=repo_root,
    )
    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 and not stdout:
        return {
            "status": "FAIL",
            "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[-2000:],
        }
    if not stdout:
        return {"status": "SKIP", "reason": "empty_guard_output"}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "status": "BLOCKED",
            "reason": "invalid_guard_json",
            "stdout_tail": stdout[-2000:],
        }
    if not isinstance(payload, dict):
        return {"status": "BLOCKED", "reason": "guard_not_object"}
    status = str(payload.get("status") or "FAIL")
    return {
        "status": status,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "raw": payload,
        "exit_code": proc.returncode,
    }


def preflight_implies_archive_gaps(preflight: dict[str, Any]) -> list[str]:
    """Machine-gap строки, принудительно ведущие к NO_GO (merge в gap_details)."""
    lines: list[str] = []
    if not preflight.get("change_exists"):
        lines.append("CHANGE_MISSING: openspec/changes/<id> not found")
    meta = preflight.get("artifact_bundle_meta") or {}
    missing = meta.get("missing") or []
    if "tasks.md" in missing:
        lines.append("PLANNING_GAP: tasks.md missing")
    tasks = preflight.get("tasks_progress") or {}
    remaining = int(tasks.get("remaining") or 0)
    if remaining > 0:
        lines.append(
            f"IMPLEMENTATION_TRACK_INCOMPLETE: {remaining} open apply-track checkbox(es)"
        )
    rollup = preflight.get("verify_receipt_rollup") or {}
    for gap in rollup.get("receipt_gaps") or []:
        if isinstance(gap, str) and gap.strip():
            lines.append(gap.strip())
    split_marker = preflight.get("apply_completion_status")
    if split_marker == "IMPL_GAPS":
        lines.append("IMPL_DRIFT: split-apply classifier reports IMPL_GAPS since last impl hop")
    lines.extend(preflight_implies_gaps(preflight))
    gate = preflight.get("channel_archive_gate") or {}
    if gate.get("status") == "FAIL":
        msg = str(gate.get("message") or gate.get("code") or "channel archive gate FAIL")
        lines.append(f"CHANNEL_ARCHIVE_GATE: {msg}")
    pa = preflight.get("product_acceptance") or {}
    pa_status = str(pa.get("status") or "")
    if pa_status in ("FAIL", "BLOCKED"):
        lines.append(f"PRODUCT_ACCEPTANCE_{pa_status}: see {pa.get('source') or 'evidence'}")
    elif pa_status == "NOT_VERIFIED" and str(pa.get("required") or "").upper() in (
        "TRUE",
        "NOT_CONFIRMED",
    ):
        lines.append(
            "PRODUCT_ACCEPTANCE_NOT_VERIFIED: PA required or not confirmed for archive"
        )
    contract = preflight.get("config_contract") or {}
    if contract.get("status") == "FAIL":
        lines.append("CONFIG_CONTRACT_FAIL: openspec config contract check failed")
    return lines


def collect_deterministic_preflight(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Machine-контекст PHASE_ARCHIVE для prompt/receipt (read-only)."""
    root = change_root(change_id, repo_root=repo_root)
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    tasks_progress = load_tasks_progress(change_id, repo_root=repo_root)
    verify_receipt_rollup = collect_verify_receipt_rollup(change_id, repo_root=repo_root)
    split_apply = run_split_apply_completion(change_id, repo_root=repo_root)
    runner_missing = (
        isinstance(split_apply, dict)
        and split_apply.get("status") == "BLOCKED"
        and split_apply.get("reason") == "completion_runner_missing"
    )
    apply_completion_status = (
        "NOT_EVALUATED"
        if runner_missing
        else (split_apply.get("marker") if isinstance(split_apply, dict) else "NOT_EVALUATED")
    )
    channel_gate = run_channel_archive_gate(change_id, repo_root=repo_root)
    product_acceptance = evaluate_product_acceptance(change_id, repo_root=repo_root)
    preflight_warnings: list[str] = []
    if channel_gate.get("status") == "SKIP":
        preflight_warnings.append(
            f"channel_archive_gate skipped: {channel_gate.get('reason', 'unknown')}"
        )
    out: dict[str, Any] = {
        "change_id": change_id,
        "change_exists": root.is_dir(),
        "artifact_bundle_meta": {
            "missing": bundle["missing"],
            "spec_paths": bundle.get("spec_paths") or [],
        },
        "tasks_progress": tasks_progress,
        "implementation_track": tasks_progress,
        "verify_receipt_rollup": verify_receipt_rollup,
        "config_contract": run_config_contract_check(repo_root=repo_root),
        "channel_archive_gate": channel_gate,
        "product_acceptance": product_acceptance,
        "apply_completion_status": apply_completion_status,
        "warnings": preflight_warnings or None,
    }
    if not runner_missing:
        out["split_apply_completion"] = split_apply
    return out
