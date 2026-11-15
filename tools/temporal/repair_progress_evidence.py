"""
Durable repair-progress evidence for Temporal review/repair loops.

Prose orch tokens (REPAIRED_THIS_BATCH, FIX_NOW) are hints only. Product
worktree mutation and materialized closed_fixed receipts override false NO.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from finding_filter import PRODUCT_LOCATION_PREFIXES, is_product_repo_path
from models import RepairRunResult

MATERIALIZE_DIR = Path("opsx/runs/bug-cases/materialize")
CLOSED_FIXED_STATUS = "closed_fixed"
DEFERRED_STATUSES = frozenset({"deferred_product_bug", "deferred", "tech_debt"})


@dataclass(frozen=True)
class RepairProgressEvidence:
    product_mutation: bool
    closed_fixed_count: int
    deferred_product_bug_count: int

    @property
    def durable_progress(self) -> bool:
        return self.product_mutation or self.closed_fixed_count > 0


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return ""
    return result.stdout


def product_worktree_fingerprint(repo_path: str | Path) -> str:
    """Stable fingerprint of product-lane dirty state vs HEAD."""
    root = Path(repo_path)
    parts: list[str] = []
    for prefix in PRODUCT_LOCATION_PREFIXES:
        prefix = prefix.rstrip("/")
        diff = _run_git(root, "diff", "HEAD", "--", prefix)
        if diff:
            parts.append(f"diff:{prefix}:{diff}")
        cached = _run_git(root, "diff", "--cached", "HEAD", "--", prefix)
        if cached:
            parts.append(f"cached:{prefix}:{cached}")
    status = _run_git(root, "status", "--porcelain")
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if is_product_repo_path(path):
            parts.append(f"status:{line}")
    return "\n".join(sorted(parts))


def scan_materialize_since(
    repo_path: str | Path,
    since_monotonic: float,
) -> RepairProgressEvidence:
    """Count materialize receipts written during the repair window."""
    root = Path(repo_path)
    materialize = resource_path(root, MATERIALIZE_DIR)
    closed_fixed = 0
    deferred = 0
    if not materialize.is_dir():
        return RepairProgressEvidence(False, 0, 0)

    for path in materialize.glob("*.json"):
        try:
            if path.stat().st_mtime < since_monotonic:
                continue
        except OSError:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        status = str(payload.get("terminal_status", "")).lower()
        if status == CLOSED_FIXED_STATUS:
            closed_fixed += 1
        elif status in DEFERRED_STATUSES:
            deferred += 1
    return RepairProgressEvidence(False, closed_fixed, deferred)


def collect_repair_progress_evidence(
    repo_path: str | Path,
    *,
    before_fingerprint: str,
    since_monotonic: float,
) -> RepairProgressEvidence:
    after_fp = product_worktree_fingerprint(repo_path)
    product_mutation = before_fingerprint != after_fp
    materialize = scan_materialize_since(repo_path, since_monotonic)
    return RepairProgressEvidence(
        product_mutation=product_mutation,
        closed_fixed_count=materialize.closed_fixed_count,
        deferred_product_bug_count=materialize.deferred_product_bug_count,
    )


def reconcile_repaired_this_batch(
    parsed: RepairRunResult,
    evidence: RepairProgressEvidence,
) -> RepairRunResult:
    """Apply durable evidence over prose parser output."""
    if evidence.durable_progress:
        parsed.repaired_this_batch = True
        return parsed

    if (
        evidence.deferred_product_bug_count > 0
        and evidence.closed_fixed_count == 0
        and not evidence.product_mutation
    ):
        parsed.repaired_this_batch = False
        return parsed

    return parsed


def apply_durable_repair_progress(
    parsed: RepairRunResult,
    repo_path: str | Path | None,
    *,
    before_fingerprint: str,
    since_monotonic: float,
) -> RepairRunResult:
    if not repo_path:
        return parsed
    evidence = collect_repair_progress_evidence(
        repo_path,
        before_fingerprint=before_fingerprint,
        since_monotonic=since_monotonic,
    )
    return reconcile_repaired_this_batch(parsed, evidence)
