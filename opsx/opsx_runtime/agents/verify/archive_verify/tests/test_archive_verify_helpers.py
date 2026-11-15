"""Unit tests for PHASE_ARCHIVE verify agent helpers."""

from __future__ import annotations

from agents.verify.archive_verify.preflight import (
    _count_tasks_markers,
    evaluate_product_acceptance,
    preflight_implies_archive_gaps,
)
from agents.verify.archive_verify.report import format_verify_archive_report
from agents.verify.archive_verify.schemas import VerifyArchiveAnswer


def test_count_tasks_markers() -> None:
    text = """
- [x] done one
- [ ] open one
- [X] done two
"""
    stats = _count_tasks_markers(text)
    assert stats == {"total": 3, "completed": 2, "remaining": 1}


def test_implementation_track_excludes_post_impl_section() -> None:
    text = """
- [x] impl done
- [ ] impl open
## Post-implementation (no checkbox)
- [ ] owner only — not counted
"""
    stats = _count_tasks_markers(text)
    assert stats["remaining"] == 1
    assert stats["total"] == 2


def test_preflight_implies_archive_gaps_incomplete_tasks(tmp_path) -> None:
    change_id = "pa-demo"
    root = tmp_path / "openspec" / "changes" / change_id
    root.mkdir(parents=True)
    (root / "tasks.md").write_text("- [ ] open task", encoding="utf-8")
    preflight = {
        "change_exists": True,
        "artifact_bundle_meta": {"missing": []},
        "tasks_progress": {"total": 1, "completed": 0, "remaining": 1},
        "apply_completion_status": "IMPL_OK",
        "config_contract": {"status": "PASS"},
        "channel_archive_gate": {"status": "SKIP"},
        "product_acceptance": {"status": "NOT_APPLICABLE"},
    }
    gaps = preflight_implies_archive_gaps(preflight)
    assert any("IMPLEMENTATION_TRACK_INCOMPLETE" in g for g in gaps)


def test_evaluate_product_acceptance_not_applicable(tmp_path) -> None:
    change_id = "pa-demo"
    ev = tmp_path / "openspec" / "changes" / change_id / "evidence"
    ev.mkdir(parents=True)
    (ev / "product-acceptance-applicability.md").write_text(
        "| `PRODUCT_ACCEPTANCE_REQUIRED` | **NOT_REQUIRED** |",
        encoding="utf-8",
    )
    result = evaluate_product_acceptance(change_id, repo_root=tmp_path)
    assert result["status"] == "NOT_APPLICABLE"


def test_format_verify_archive_report_marker() -> None:
    answer = VerifyArchiveAnswer(
        verdict="NO_GO",
        completeness_summary="tasks open",
        correctness_summary="ok",
        coherence_summary="ok",
        product_acceptance_status="NOT_VERIFIED",
        gap_details=["IMPLEMENTATION_TRACK_INCOMPLETE: 1 open"],
        warnings=[],
        local_hop_copy_block="Workspace: /tmp\nChange: x\nmode=archive",
    )
    text = format_verify_archive_report(
        change_id="x",
        answer=answer,
        preflight={"change_exists": True, "tasks_progress": {"completed": 0, "total": 1}},
        marker="❌ NO_GO",
    )
    assert "mode=archive" in text
    assert "❌ NO_GO" in text
