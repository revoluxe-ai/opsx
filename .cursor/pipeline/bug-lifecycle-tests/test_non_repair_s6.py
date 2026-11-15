"""S6 non-repair durable disposition (defer)."""

from __future__ import annotations

from bug_lifecycle.materialize_bug_queue import MaterializeContext, mode_intake, mode_update_disposition
from bug_lifecycle.orch_mission_runtime import advance, run_to_batch_clear


def test_defer_disposition_materialized_at_s6(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    mission = "ORCH-defer"
    request = {
        "mission": mission,
        "execution_id": "exec-defer",
        "head": "c" * 40,
        "change": "chg",
        "findings": [{"location": "app/a.py:1", "finding": "F-def"}],
    }
    step0 = advance(root, request)
    assert step0["STATUS"] == "STAGE_ADVANCED"
    ctx = MaterializeContext(
        repo_root=root,
        mission_id=mission,
        execution_id="exec-defer",
        head="c" * 40,
        change_id="chg",
        production=False,
    )
    case_id = step0["WORK_ITEMS"][0]["case_id"]
    mode_update_disposition(
        ctx,
        case_id=case_id,
        finding_id="F-def",
        disposition="DEFER_NEXT_CHANGE",
        plan_id="P1",
        work_item_id="W1",
    )
    for _ in range(20):
        step = advance(root, request)
        if step.get("BUG_BATCH_CLEAR") == "YES":
            break
        if step.get("STATUS") in {"DISPATCH_REQUIRED", "FAIL_CLOSED"}:
            break
    mat = root / "opsx/runs/bug-cases/materialize/deferred-ORCH-defer-F-def.json"
    assert mat.is_file()
