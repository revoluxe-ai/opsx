"""Durable adversarial-closure gate acks for archive verify (OPSX-GAME-ARCHIVE-BUGBOT-01).

Temporal terminal runs write machine-readable acks under:
`opsx/runs/temporal/gates/adversarial-closure/<change_id>/`
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_store import durable_gate_dir

CONTRACT_ID = "OPSX-GAME-TEMPORAL-ADVERSARIAL-CLOSURE-01"

PIPELINE_CLEAN = {
    "bugbot": "BUGBOT_CLEAN",
    "project-bug-hunt": "HUNT_CLEAN",
    "security": "SECURITY_CLEAN",
}

REQUIRED_TIERS = ("low", "medium", "high")


def _repo_root(repo: Path) -> Path:
    return Path(repo).resolve()


def ledger_snapshot(repo: Path, change_id: str) -> dict[str, Any] | None:
    plan_file = (
        _repo_root(repo)
        / "openspec"
        / "changes"
        / change_id
        / "bounded-verify"
        / "plan.json"
    )
    if not plan_file.is_file():
        return None
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    snap = plan.get("implementation_snapshot")
    if not isinstance(snap, dict):
        return None
    return {
        "plan_id": plan.get("plan_id"),
        "implementation_snapshot": snap,
    }


def git_head_sha(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(_repo_root(repo)),
        check=False,
        capture_output=True,
        text=True,
    )
    sha = (result.stdout or "").strip()
    return sha if result.returncode == 0 and sha else None


def ack_dir(repo: Path, change_id: str) -> Path:
    return durable_gate_dir(repo) / "adversarial-closure" / change_id


def ack_path(repo: Path, change_id: str, pipeline: str, review_tier: str) -> Path:
    tier = (review_tier or "low").strip().lower()
    return ack_dir(repo, change_id) / f"{pipeline}-{tier}.json"


def write_adversarial_ack(
    *,
    repo: Path,
    change_id: str,
    pipeline: str,
    review_tier: str,
    repair_tier: str,
    outcome: str,
    run_summary: dict[str, Any],
    model_summary: dict[str, Any] | None = None,
) -> Path | None:
    """Persist terminal adversarial ack when outcome is clean for the pipeline."""
    clean = PIPELINE_CLEAN.get(pipeline)
    if not clean or outcome != clean:
        return None
    if not change_id or not change_id.strip():
        return None

    bound = ledger_snapshot(repo, change_id)
    if not bound:
        return None

    dest = ack_path(repo, change_id, pipeline, review_tier)
    dest.parent.mkdir(parents=True, exist_ok=True)

    review_spec = (model_summary or {}).get("steps", {}).get("review", {})
    repair_spec = (model_summary or {}).get("steps", {}).get("repair", {})

    doc = {
        "schema_version": 1,
        "contract_id": CONTRACT_ID,
        "change_id": change_id,
        "pipeline": pipeline,
        "review_tier": review_tier,
        "repair_tier": repair_tier,
        "outcome": outcome,
        "plan_id": bound.get("plan_id"),
        "implementation_snapshot": bound["implementation_snapshot"],
        "product_head_sha": git_head_sha(repo),
        "review_scope": {
            "base_ref": run_summary.get("base"),
            "head_ref": run_summary.get("head"),
            "diff_mode": "branch_changes",
        },
        "temporal": {
            "workflow_id": run_summary.get("workflow_id"),
            "result_file": run_summary.get("result_file"),
        },
        "model_authority": {
            "review_cli_model": review_spec.get("cli_model"),
            "repair_cli_model": repair_spec.get("cli_model"),
            "config_source": (model_summary or {}).get("source"),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result_origin": "TEMPORAL_TERMINAL",
    }
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def resolve_change_for_run(resolved: dict[str, Any]) -> str | None:
    change = resolved.get("change")
    if isinstance(change, str) and change.strip():
        return change.strip()
    return None
