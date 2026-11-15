"""Hermetic tests for S3 disposition routing matrix (D1)."""

from __future__ import annotations

from bug_lifecycle.disposition_route import classify_route, route_work_items
from bug_lifecycle.materialize_bug_queue import MaterializeContext, mode_intake


def _seed_finding(root, case_id: str, finding_id: str, disposition: str, extra: str = "") -> None:
    path = root / "opsx/runs/bug-cases" / case_id / "findings" / f"{finding_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"FINDING_ID: {finding_id}",
                f"DISPOSITION: {disposition}",
                extra,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_classify_route_matrix() -> None:
    assert classify_route("TECH_DEBT_ONLY") == "TECH_DEBT_EVOLUTION"
    assert classify_route("GLOBAL_RELEASE_BLOCKER") == "GLOBAL_RELEASE_BLOCKER"
    assert classify_route("NEEDS_ADJUDICATION") == "BLOCKED_ADJUDICATION"
    assert classify_route("AUTHORITY_CONFLICT") == "BLOCKED_ADJUDICATION"
    assert classify_route("MIXED_DISPOSITION") == "MIXED_SPLIT"
    assert classify_route("MUST_FIX_CURRENT_CHANGE") == "MUST_FIX_REPAIR"
    assert classify_route("FALSE_POSITIVE_CONFIRMED") == "NON_REPAIR"


def test_mixed_disposition_requires_split(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    ctx = MaterializeContext(
        repo_root=root,
        mission_id="ORCH-mix",
        execution_id="exec-mix",
        head="a" * 40,
        change_id="chg",
        production=False,
    )
    intake = mode_intake(ctx, finding_id="F-mix", location="app/a.py:1", raw_report="x")
    _seed_finding(
        root,
        intake["CASE_ID"],
        "F-mix",
        "MIXED_DISPOSITION",
        "REQUIRES_ROUTING_SPLIT: YES",
    )
    routed, blocked = route_work_items(
        root,
        [{"finding_id": "F-mix", "case_id": intake["CASE_ID"], "location": "app/a.py:1"}],
    )
    assert not routed
    assert blocked


def test_mixed_disposition_split_complete_routes(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    ctx = MaterializeContext(
        repo_root=root,
        mission_id="ORCH-mix2",
        execution_id="exec-mix2",
        head="b" * 40,
        change_id="chg",
        production=False,
    )
    intake = mode_intake(ctx, finding_id="F-mix2", location="app/b.py:2", raw_report="x")
    _seed_finding(
        root,
        intake["CASE_ID"],
        "F-mix2",
        "MIXED_DISPOSITION",
        "\n".join(
            [
                "ROUTING_SPLIT_COMPLETE: YES",
                "MUST_FIX_CURRENT_CHANGE_MEMBER_IDS: [M-1]",
            ]
        ),
    )
    routed, blocked = route_work_items(
        root,
        [{"finding_id": "F-mix2", "case_id": intake["CASE_ID"], "location": "app/b.py:2"}],
    )
    assert not blocked
    assert routed[0]["route"] == "MIXED_SPLIT"
    assert routed[0]["member_ids"] == ["M-1"]
