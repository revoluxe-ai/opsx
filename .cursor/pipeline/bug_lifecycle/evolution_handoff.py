"""S7–S9 evolution/debt handoff artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .receipts import write_receipt


def write_evolution_queue(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    work_items: list[dict[str, Any]],
) -> dict[str, Any]:
    path = repo_root / "opsx/runs/orch-missions" / mission_id / "evolution-queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "finding_id": w["finding_id"],
            "case_id": w["case_id"],
            "route": w.get("route", "MUST_FIX_REPAIR"),
        }
        for w in work_items
    ]
    payload = {"mission_id": mission_id, "execution_id": execution_id, "entries": entries}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    receipt = write_receipt(
        repo_root,
        mission_id=mission_id,
        stage="S7_EVOLUTION_QUEUE",
        execution_id=execution_id,
        head=head,
        writer="/opsx-orch-bug-fix",
        body={"queue_path": str(path.name), "entry_count": len(entries)},
    )
    return {"STATUS": "EVOLUTION_QUEUE_WRITTEN", "RECEIPT": receipt, "ENTRIES": len(entries)}


def write_evolution_handoff(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
) -> dict[str, Any]:
    path = repo_root / "opsx/runs/orch-missions" / mission_id / "evolution-handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": mission_id,
        "execution_id": execution_id,
        "handoff": "RETURN_TO_EVOLUTION_BOARD",
        "status": "RECORDED",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    receipt = write_receipt(
        repo_root,
        mission_id=mission_id,
        stage="S8_EVOLUTION_HANDOFF",
        execution_id=execution_id,
        head=head,
        writer="/opsx-orch-bug-fix",
        body={"handoff_path": str(path.name)},
    )
    return {"STATUS": "HANDOFF_RECORDED", "RECEIPT": receipt}


def write_debt_board_verified(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
) -> dict[str, Any]:
    from .debt_board import open_debt_for_mission, verify_no_open_debt

    verification, rows, reason = verify_no_open_debt(
        repo_root, mission_id=mission_id, execution_id=execution_id
    )
    if verification == "UNKNOWN":
        return {
            "STATUS": "PAUSE",
            "DEBT_BOARD": "UNKNOWN",
            "REASON": reason,
        }
    if verification == "PAUSE":
        return {
            "STATUS": "FAIL_CLOSED",
            "DEBT_BOARD": "PAUSE",
            "REASON": reason,
        }
    if verification == "OPEN_DEBT":
        return {
            "STATUS": "FAIL_CLOSED",
            "REASON": "open_debt_remaining",
            "OPEN_DEBT": rows,
        }
    if verification != "VERIFIED_NO_OPEN_DEBT":
        return {"STATUS": "PAUSE", "DEBT_BOARD": "UNKNOWN", "REASON": reason}
    path = repo_root / "opsx/runs/orch-missions" / mission_id / "debt-board-verified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": mission_id,
        "execution_id": execution_id,
        "open_debt_count": len(open_debt_for_mission(repo_root, mission_id=mission_id, execution_id=execution_id)),
        "verified": "NO_OPEN_DEBT",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    receipt = write_receipt(
        repo_root,
        mission_id=mission_id,
        stage="S9_DEBT_BOARD",
        execution_id=execution_id,
        head=head,
        writer="/opsx-orch-bug-fix",
        body={"debt_board": "VERIFIED_NO_OPEN_DEBT", "open_debt_count": 0},
    )
    return {"STATUS": "DEBT_VERIFIED", "RECEIPT": receipt}
