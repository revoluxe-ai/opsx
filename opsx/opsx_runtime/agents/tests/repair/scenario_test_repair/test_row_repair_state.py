"""Graph-hop repair state: source digest binding for row walk."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot


def _load_row_cycle():
    path = (
        Path(__file__).resolve().parents[3]
        / "verify/scenario_coverage_verify/row_cycle.py"
    )
    spec = importlib.util.spec_from_file_location("row_cycle_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_state_refreshes_source_digest_from_repo(repo_root: Path) -> None:
    row_cycle = _load_row_cycle()
    change_id = "external-client-notifications-pilot"
    sid = "openspec/changes/demo/spec.md::x"
    current = input_snapshot(repo_root, change_id)["source_digest"]
    check = {
        "scenario_coverage_answer": {
            "verdict": "SCENARIO_TEST_COVERAGE_FAIL",
            "source_digest": "stale-row-audit-digest",
            "gaps": [{"scenario_id": sid, "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST"}],
        },
        "scenario_coverage_source_snapshot": {"source_digest": "stale-row-audit-digest"},
    }
    repair_state = row_cycle._repair_state(
        {"run_uuid": "walk-run", "change_id": change_id},
        check,
        sid,
        repo=repo_root,
        change_id=change_id,
    )
    assert repair_state["scenario_coverage_source_snapshot"]["source_digest"] == current
    assert repair_state["scenario_coverage_answer"]["source_digest"] == current
    assert repair_state["scenario_test_repair_graph_hop"] is True


@pytest.mark.asyncio
async def test_graph_hop_passes_preflight_after_repair_state_refresh(
    audit_context, repo_root,
):
    from agents.repair.scenario_test_repair.agent import validate_input

    row_cycle = _load_row_cycle()
    sid = audit_context["scenario_coverage_answer"]["gaps"][0]["scenario_id"]
    stale_check = {
        "scenario_coverage_answer": {
            **audit_context["scenario_coverage_answer"],
            "source_digest": "stale",
        },
        "scenario_coverage_source_snapshot": {"source_digest": "stale"},
    }
    state = row_cycle._repair_state(
        audit_context,
        stale_check,
        sid,
        repo=repo_root,
        change_id=audit_context["change_id"],
    )
    assert validate_input(
        state, repo_root=repo_root, change_id=audit_context["change_id"],
    ) is None


def test_journal_records_repair_block_reason(tmp_path: Path) -> None:
    from agents.verify.scenario_coverage_verify.row_cycle import RowWalkJournal

    journal = RowWalkJournal(tmp_path / "row_walk_journal.json")
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    journal.mark_repair_finished(
        sid,
        "tok",
        "BLOCKED",
        repair_block_reason="REPAIR_SOURCE_STALE: digest mismatch",
    )
    assert journal.row(sid)["repair_block_reason"].startswith("REPAIR_SOURCE_STALE")
