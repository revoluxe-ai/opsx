"""D4: durable dispatch idempotency and worker UNKNOWN → PAUSE."""

from __future__ import annotations

import json

from bug_lifecycle.bug_team_authority import run_s4
from bug_lifecycle.bug_team_transport import record_dispatch
from bug_lifecycle.materialize_bug_queue import MaterializeContext, mode_intake
from bug_lifecycle.worktree_ownership import reconcile_worktree_ownership


def _setup_s35(root, mission: str, finding: str) -> tuple[str, str]:
    ctx = MaterializeContext(
        repo_root=root,
        mission_id=mission,
        execution_id="exec-d4",
        head="d" * 40,
        change_id="chg",
        production=True,
    )
    intake = mode_intake(ctx, finding_id=finding, location="app/x.py:1", raw_report="x")
    case_id = intake["CASE_ID"]
    wi = "WI-d4"
    reconcile_worktree_ownership(
        root,
        mission_id=mission,
        execution_id="exec-d4",
        work_items=[
            {
                "finding_id": finding,
                "work_item_id": wi,
                "case_id": case_id,
                "location": "app/x.py:1",
                "route": "MUST_FIX_REPAIR",
            }
        ],
        delta_map={"app/x.py:1": {"work_item_id": wi}},
    )
    return case_id, wi


def test_dispatch_idempotent_no_duplicate_writes(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    mission = "ORCH-d4-idem"
    case_id, wi = _setup_s35(root, mission, "F-d4")
    request = {
        "mission": mission,
        "execution_id": "exec-d4",
        "head": "d" * 40,
        "change": "chg",
    }
    first = record_dispatch(
        root,
        mission_id=mission,
        execution_id="exec-d4",
        head="d" * 40,
        case_id=case_id,
        finding_id="F-d4",
        plan_id="P1",
        work_item_id=wi,
    )
    second = record_dispatch(
        root,
        mission_id=mission,
        execution_id="exec-d4",
        head="d" * 40,
        case_id=case_id,
        finding_id="F-d4",
        plan_id="P1",
        work_item_id=wi,
    )
    assert first["IDEMPOTENT"] == "NO"
    assert second["IDEMPOTENT"] == "YES"
    path = root / "opsx/runs/orch-missions" / mission / "bug-team-dispatch" / "F-d4.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["dispatch_identity"] == first["DISPATCH_ID"]


def test_unknown_worker_liveness_pauses(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    mission = "ORCH-d4-pause"
    case_id, wi = _setup_s35(root, mission, "F-pause")
    request = {
        "mission": mission,
        "execution_id": "exec-d4",
        "head": "d" * 40,
        "worker_liveness": "UNKNOWN",
    }
    outcome = run_s4(
        root,
        request,
        case_id=case_id,
        finding_id="F-pause",
        plan_id="P1",
        work_item_id=wi,
        production=True,
    )
    assert outcome.get("STATUS") == "PAUSE"
    assert outcome.get("REASON") == "STOP_UNCONFIRMED_worker_liveness"
