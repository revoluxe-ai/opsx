"""Mission-scoped family plan persist (STROY: non-durable plan under orch-missions)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .receipts import write_receipt

WRITER = "/opsx-bug-family-plan"


def persist_family_plan(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    change_id: str,
    clean_findings: list[dict[str, str]],
) -> dict[str, Any]:
    plan_id = f"PLAN-{mission_id}"
    work_items = []
    for index, row in enumerate(clean_findings, start=1):
        finding = row.get("finding") or f"F-{index}"
        work_items.append(
            {
                "work_item_id": f"WI-{mission_id}-{index}",
                "finding_id": finding,
                "location": row.get("location", ""),
                "kind": "ATOMIC_BUG" if len(clean_findings) == 1 else "BUG_FAMILY_MEMBER",
            }
        )
    plan = {
        "schema_version": "opsx.orch.family_plan.v1",
        "plan_id": plan_id,
        "change_id": change_id,
        "mission_id": mission_id,
        "work_items": work_items,
    }
    plan_path = repo_root / "opsx" / "runs" / "orch-missions" / mission_id / "family-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = plan_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, plan_path)
    receipt = write_receipt(
        repo_root,
        mission_id=mission_id,
        stage="S1b_FAMILY_PLAN_PERSIST",
        execution_id=execution_id,
        head=head,
        writer=WRITER,
        body={"plan_id": plan_id, "work_item_count": len(work_items), "plan_path": str(plan_path)},
    )
    return {
        "STATUS": "PERSISTED",
        "PLAN_ID": plan_id,
        "WORK_ITEMS": work_items,
        "RECEIPT": receipt,
    }
