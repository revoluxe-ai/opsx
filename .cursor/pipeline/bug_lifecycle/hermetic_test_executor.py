"""Hermetic test executor — only inside registered hermetic workspace boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import product_writer_global
from .bug_team_transport import write_execution_terminal
from .hermetic_boundary import assert_hermetic_workspace
from .receipts import write_receipt

WRITER = "/opsx-bug-team"
PROVENANCE = "HERMETIC_TEST_EXECUTOR"


def apply_bug_team_terminal(
    repo_root: Path,
    request: dict[str, Any],
    *,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
) -> dict[str, Any]:
    try:
        assert_hermetic_workspace(repo_root)
    except Exception:
        return {"STATUS": "FAIL_CLOSED", "REASON": "HERMETIC_BOUNDARY_VIOLATION"}
    mission_id = str(request["mission"])
    execution_id = str(request["execution_id"])
    head = str(request.get("head") or "")
    product_writer_global.acquire(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        finding_id=finding_id,
    )
    case_root = repo_root / "opsx" / "runs" / "bug-cases" / case_id
    results = case_root / "results"
    results.mkdir(parents=True, exist_ok=True)
    green = results / f"GREEN-{finding_id}.md"
    green.write_text(
        "\n".join(
            [
                f"TEST_ID: RED-{finding_id}",
                f"BUG_CASE_ID: {case_id}",
                f"FINDING_ID: {finding_id}",
                "COMMAND: /opsx-test verify mode",
                "PRODUCTION_HASH_AFTER: hermetic-unchanged",
                "TEST_CONTENT_SHA256: hermetic-sha",
                "STATUS: GREEN_CONFIRMED",
                "TARGETED_REGRESSIONS: none",
                f"PROVENANCE: {PROVENANCE}",
                f"WORKER_EXECUTION_ID: {execution_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    packet = results / f"BUG-TEAM-{finding_id}.json"
    packet.write_text(
        json.dumps(
            {
                "CLOSURE_READINESS": "READY_FOR_CLOSURE",
                "BLOCKING_FINDINGS": "NONE",
                "CASE_STATUS": "CLOSING",
                "mission_id": mission_id,
                "case_id": case_id,
                "finding_id": finding_id,
                "plan_id": plan_id,
                "work_item_id": work_item_id,
                "execution_id": execution_id,
                "head": head,
                "PROVENANCE": PROVENANCE,
                "EXECUTION_TERMINAL": "CONFIRMED",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = write_receipt(
        repo_root,
        mission_id=mission_id,
        stage="S4_BUG_TEAM_TERMINAL",
        execution_id=execution_id,
        head=head,
        writer=WRITER,
        body={
            "case_id": case_id,
            "finding_id": finding_id,
            "provenance": PROVENANCE,
            "closure_readiness": "READY_FOR_CLOSURE",
        },
    )
    write_execution_terminal(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        source=PROVENANCE,
    )
    product_writer_global.release_on_terminal(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        execution_terminal="CONFIRMED",
        head=head or None,
    )
    return {"STATUS": "READY_FOR_CLOSURE", "RECEIPT": receipt, "PROVENANCE": PROVENANCE}
