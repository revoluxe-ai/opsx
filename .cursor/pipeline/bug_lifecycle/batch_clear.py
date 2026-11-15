"""BUG_BATCH_CLEAR verification with execution/revision identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .terminal_gate import verify_terminal_gate


def verify_batch_clear(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    finding_ids: list[str],
    work_items: list[dict[str, Any]] | None = None,
    production: bool = True,
) -> dict[str, Any]:
    items = work_items or [{"finding_id": fid, "case_id": ""} for fid in finding_ids]
    if work_items is None:
        resolved: list[dict[str, Any]] = []
        for fid in finding_ids:
            resolved.append({"finding_id": fid, "case_id": _case_for_finding(repo_root, fid)})
        items = resolved
    return verify_terminal_gate(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        work_items=items,
        production=production,
    )


def _case_for_finding(repo_root: Path, finding_id: str) -> str:
    root = repo_root / "opsx/runs/bug-cases"
    if not root.is_dir():
        return ""
    for case_dir in root.iterdir():
        if (case_dir / "findings" / f"{finding_id}.md").is_file():
            return case_dir.name
    return ""
