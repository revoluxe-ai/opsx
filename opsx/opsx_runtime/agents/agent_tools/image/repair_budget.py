"""Бюджет попыток и дедлайн image_task (переживает повторные tool calls)."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.agent_tools.image.schemas import VisualFinding, VisualReviewVerdict

DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_DEADLINE_SECONDS = 3600

_REPAIR_STATE = "REPAIR-STATE.json"


def repair_state_rel(change_id: str, task_id: str) -> str:
    return f"openspec/changes/{change_id}/apply-image/{task_id}/{_REPAIR_STATE}"


def findings_fingerprint(findings: list[VisualFinding]) -> str:
    """Стабильный отпечаток набора findings (не output revision)."""
    if not findings:
        return "none"
    parts = sorted(f"{f.code}:{f.severity}" for f in findings)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True, slots=True)
class RepairBudgetPolicy:
    """Лимиты одной image_task."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    deadline_seconds: int = DEFAULT_DEADLINE_SECONDS


def load_repair_state(repo_root: Path, change_id: str, task_id: str) -> dict[str, Any]:
    path = resource_path(repo_root, repair_state_rel(change_id, task_id))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_repair_state(
    repo_root: Path,
    change_id: str,
    task_id: str,
    state: dict[str, Any],
) -> None:
    path = resource_path(repo_root, repair_state_rel(change_id, task_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_repair_state(
    repo_root: Path,
    change_id: str,
    task_id: str,
    policy: RepairBudgetPolicy,
) -> dict[str, Any]:
    state = load_repair_state(repo_root, change_id, task_id)
    if state:
        return state
    now = int(time.time())
    state = {
        "task_id": task_id,
        "started_at_unix": now,
        "deadline_unix": now + policy.deadline_seconds,
        "max_attempts": policy.max_attempts,
        "attempts": [],
        "attempts_used": 0,
        "last_finding_fingerprint": None,
        "exhausted": False,
    }
    save_repair_state(repo_root, change_id, task_id, state)
    return state


def budget_exhausted(state: dict[str, Any]) -> tuple[bool, str | None]:
    """(exhausted, code)."""
    if state.get("exhausted"):
        return True, str(state.get("exhaust_code") or "IMAGE_REPAIR_BUDGET_EXHAUSTED")
    now = int(time.time())
    deadline = int(state.get("deadline_unix") or 0)
    if deadline and now > deadline:
        return True, "IMAGE_TASK_DEADLINE"
    used = int(state.get("attempts_used") or 0)
    max_a = int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    if used >= max_a:
        return True, "IMAGE_REPAIR_BUDGET_EXHAUSTED"
    return False, None


def visual_counts_as_progress(
    *,
    prior_finding_codes: set[str] | None,
    verdict: VisualReviewVerdict,
) -> bool:
    """Смена байтов без улучшения visual — не прогресс."""
    if verdict.status == "PASS":
        return True
    if not prior_finding_codes:
        return False
    new_codes = {f.code for f in verdict.findings}
    resolved = prior_finding_codes - new_codes
    return bool(resolved)


def record_attempt(
    state: dict[str, Any],
    *,
    mode: str,
    output_revision: str,
    visual: VisualReviewVerdict | None,
    made_progress: bool,
    note: str = "",
) -> dict[str, Any]:
    """Учесть одну попытку; вернуть обновлённый state."""
    used = int(state.get("attempts_used") or 0) + 1
    fp = findings_fingerprint(visual.findings) if visual else None
    entry = {
        "attempt": used,
        "mode": mode,
        "output_revision": output_revision,
        "visual_status": visual.status if visual else None,
        "finding_fingerprint": fp,
        "made_progress": made_progress,
        "note": note,
        "at_unix": int(time.time()),
    }
    attempts = list(state.get("attempts") or [])
    attempts.append(entry)
    state = {
        **state,
        "attempts": attempts,
        "attempts_used": used,
        "last_finding_fingerprint": fp,
    }
    if visual and visual.status != "PASS":
        state["last_failed_fingerprint"] = fp
    return state


def mark_exhausted(state: dict[str, Any], code: str, reasons: list[str]) -> dict[str, Any]:
    return {
        **state,
        "exhausted": True,
        "exhaust_code": code,
        "exhaust_reasons": reasons,
    }


def exhaustion_reasons(state: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if int(state.get("attempts_used") or 0) >= int(state.get("max_attempts") or 0):
        reasons.append(
            f"attempt budget {state.get('attempts_used')}/{state.get('max_attempts')}",
        )
    now = int(time.time())
    if now > int(state.get("deadline_unix") or 0):
        reasons.append("task deadline exceeded")
    last = state.get("last_failed_fingerprint")
    if last:
        reasons.append(f"last visual fingerprint={last} (bytes-only changes do not count)")
    attempts = state.get("attempts") or []
    no_progress = [a for a in attempts if isinstance(a, dict) and not a.get("made_progress")]
    if len(no_progress) >= 2:
        reasons.append(f"{len(no_progress)} attempts without visual progress")
    return reasons
