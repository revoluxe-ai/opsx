"""Unit tests for native materialize + batch clear identity."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle.batch_clear import verify_batch_clear
from bug_lifecycle.materialize_bug_queue import MaterializeContext, mode_intake, mode_update_close_fixed, mode_update_disposition


def _authoritative_green_and_packet(
    root: Path,
    *,
    mission_id: str,
    case_id: str,
    finding_id: str,
    execution_id: str,
    head: str,
    work_item_id: str,
    plan_id: str,
) -> None:
    results = root / "opsx/runs/bug-cases" / case_id / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / f"GREEN-{finding_id}.md").write_text(
        "\n".join(
            [
                f"TEST_ID: RED-{finding_id}",
                f"BUG_CASE_ID: {case_id}",
                f"FINDING_ID: {finding_id}",
                "COMMAND: /opsx-test verify mode",
                "PRODUCTION_HASH_AFTER: test",
                "TEST_CONTENT_SHA256: test-sha",
                "STATUS: GREEN_CONFIRMED",
                "TARGETED_REGRESSIONS: none",
                "PROVENANCE: HOST_BUG_TEAM",
                f"WORKER_EXECUTION_ID: {execution_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (results / f"BUG-TEAM-{finding_id}.json").write_text(
        json.dumps(
            {
                "CLOSURE_READINESS": "READY_FOR_CLOSURE",
                "mission_id": mission_id,
                "case_id": case_id,
                "finding_id": finding_id,
                "execution_id": execution_id,
                "head": head,
                "work_item_id": work_item_id,
                "plan_id": plan_id,
                "EXECUTION_TERMINAL": "CONFIRMED",
                "PROVENANCE": "HOST_BUG_TEAM",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    from bug_lifecycle.bug_team_transport import write_execution_terminal

    write_execution_terminal(
        root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        source="HOST_BUG_TEAM",
    )


def test_materialize_close_requires_green_and_identity(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ctx = MaterializeContext(
        repo_root=root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        change_id="chg",
        production=True,
    )
    intake = mode_intake(ctx, finding_id="F1", location="app/a.py:1", raw_report="bug")
    _authoritative_green_and_packet(
        root,
        mission_id="M1",
        case_id=intake["CASE_ID"],
        finding_id="F1",
        execution_id="exec-1",
        head="h1",
        work_item_id="WI-1",
        plan_id="PLAN-M1",
    )
    mode_update_disposition(
        ctx,
        case_id=intake["CASE_ID"],
        finding_id="F1",
        plan_id="PLAN-M1",
        work_item_id="WI-1",
    )
    mode_update_close_fixed(ctx, case_id=intake["CASE_ID"], finding_id="F1")
    ok = verify_batch_clear(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        finding_ids=["F1"],
        work_items=[{"finding_id": "F1", "case_id": intake["CASE_ID"], "work_item_id": "WI-1"}],
        production=True,
    )
    assert ok["BUG_BATCH_CLEAR"] == "YES"
    bad = verify_batch_clear(
        root,
        mission_id="M1",
        execution_id="exec-OTHER",
        head="h1",
        finding_ids=["F1"],
        work_items=[{"finding_id": "F1", "case_id": intake["CASE_ID"], "work_item_id": "WI-1"}],
        production=True,
    )
    assert bad["BUG_BATCH_CLEAR"] == "NO"
