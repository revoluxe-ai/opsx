"""S10 terminal gate across mixed disposition classes (D3)."""

from __future__ import annotations

import json

from bug_lifecycle.materialize_bug_queue import (
    MaterializeContext,
    mode_intake,
    mode_update_defer,
    mode_update_disposition,
    mode_update_false_positive,
)
from bug_lifecycle.terminal_gate import verify_terminal_gate


def _authoritative_green_and_packet(
    root,
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


def test_s10_mixed_fp_defer_and_repair(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    mission = "ORCH-mixed-s10"
    execution_id = "exec-mixed"
    head = "d" * 40
    ctx = MaterializeContext(
        repo_root=root,
        mission_id=mission,
        execution_id=execution_id,
        head=head,
        change_id="chg",
        production=False,
    )
    intake_r = mode_intake(ctx, finding_id="F-repair", location="app/r.py:1", raw_report="r")
    intake_f = mode_intake(ctx, finding_id="F-fp", location="app/f.py:1", raw_report="f")
    intake_d = mode_intake(ctx, finding_id="F-def", location="app/d.py:1", raw_report="d")
    work_items = [
        {
            "finding_id": "F-repair",
            "case_id": intake_r["CASE_ID"],
            "work_item_id": "W-r",
            "route": "MUST_FIX_REPAIR",
            "disposition": "MUST_FIX_CURRENT_CHANGE",
        },
        {
            "finding_id": "F-fp",
            "case_id": intake_f["CASE_ID"],
            "work_item_id": "W-f",
            "route": "NON_REPAIR",
            "disposition": "FALSE_POSITIVE_CONFIRMED",
        },
        {
            "finding_id": "F-def",
            "case_id": intake_d["CASE_ID"],
            "work_item_id": "W-d",
            "route": "NON_REPAIR",
            "disposition": "DEFER_PRODUCT_BUG",
        },
    ]
    for item in work_items:
        mode_update_disposition(
            ctx,
            case_id=item["case_id"],
            finding_id=item["finding_id"],
            disposition=str(item["disposition"]),
            plan_id="P1",
            work_item_id=item["work_item_id"],
        )
        finding = root / "opsx/runs/bug-cases" / item["case_id"] / "findings" / f"{item['finding_id']}.md"
        finding.write_text(
            finding.read_text(encoding="utf-8") + f"MISSION_ID: {mission}\n",
            encoding="utf-8",
        )
    _authoritative_green_and_packet(
        root,
        mission_id=mission,
        case_id=intake_r["CASE_ID"],
        finding_id="F-repair",
        execution_id=execution_id,
        head=head,
        work_item_id="W-r",
        plan_id="P1",
    )
    from bug_lifecycle.materialize_bug_queue import mode_update_close_fixed

    mode_update_close_fixed(ctx, case_id=intake_r["CASE_ID"], finding_id="F-repair")
    mode_update_false_positive(ctx, case_id=intake_f["CASE_ID"], finding_id="F-fp")
    mode_update_defer(ctx, case_id=intake_d["CASE_ID"], finding_id="F-def")
    handoff = root / "opsx/runs/orch-missions" / mission / "evolution-handoff.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(
        '{"mission_id": "ORCH-mixed-s10", "execution_id": "exec-mixed", "handoff": "yes"}\n',
        encoding="utf-8",
    )
    verdict = verify_terminal_gate(
        root,
        mission_id=mission,
        execution_id=execution_id,
        head=head,
        work_items=work_items,
        production=False,
    )
    assert verdict["BUG_BATCH_CLEAR"] == "YES"
