"""Acceptance tests for durable repair-progress detection (BugBot loop)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from cursor_adapter import MockCursorAdapter, build_scenario
from lanes import PROJECT_BUG_HUNT_LANE
from loop_engine import ReviewRepairLoop
from models import RepairRunResult
from parsers import parse_opsx_bug_fix_result
from repair_progress_evidence import (
    RepairProgressEvidence,
    apply_durable_repair_progress,
    collect_repair_progress_evidence,
    product_worktree_fingerprint,
    reconcile_repaired_this_batch,
    scan_materialize_since,
)


def _git_init_with_product_file(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.js").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "src/app.js"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def test_a_product_mutation_overrides_prose_no(tmp_path: Path) -> None:
    _git_init_with_product_file(tmp_path)
    before_fp = product_worktree_fingerprint(tmp_path)
    (tmp_path / "src" / "app.js").write_text("v2\n", encoding="utf-8")
    raw = "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: NO\n"
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.repaired_this_batch is False
    evidence = collect_repair_progress_evidence(
        tmp_path,
        before_fingerprint=before_fp,
        since_monotonic=time.monotonic() - 1,
    )
    assert evidence.product_mutation is True
    reconciled = reconcile_repaired_this_batch(parsed, evidence)
    assert reconciled.repaired_this_batch is True


def test_b_closed_fixed_count_without_product_diff(tmp_path: Path) -> None:
    materialize = tmp_path / "opsx/runs/bug-cases/materialize"
    materialize.mkdir(parents=True)
    receipt = {
        "terminal_status": "closed_fixed",
        "work_item_id": "WI-1",
    }
    path = materialize / "closed-WI-TEST-1.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    since = time.monotonic() - 10
    evidence = scan_materialize_since(tmp_path, since)
    assert evidence.closed_fixed_count == 1
    parsed = parse_opsx_bug_fix_result(
        "STATUS: BUG_BATCH_CLEAR\nREPAIRED_THIS_BATCH: NO\n"
    )
    reconciled = reconcile_repaired_this_batch(
        parsed,
        RepairProgressEvidence(
            product_mutation=False,
            closed_fixed_count=evidence.closed_fixed_count,
            deferred_product_bug_count=0,
        ),
    )
    assert reconciled.repaired_this_batch is True


def test_c_deferred_only_not_counted_as_fixed(tmp_path: Path) -> None:
    materialize = tmp_path / "opsx/runs/bug-cases/materialize"
    materialize.mkdir(parents=True)
    defer = materialize / "defer-WI-TEST-1.json"
    defer.write_text(
        json.dumps({"terminal_status": "deferred_product_bug"}),
        encoding="utf-8",
    )
    since = time.monotonic() - 10
    evidence = scan_materialize_since(tmp_path, since)
    assert evidence.deferred_product_bug_count == 1
    assert evidence.closed_fixed_count == 0
    parsed = parse_opsx_bug_fix_result(
        "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: NO\n"
    )
    reconciled = reconcile_repaired_this_batch(parsed, evidence)
    assert reconciled.repaired_this_batch is False


def test_d_no_mutation_no_closed_fixed_stays_no_progress() -> None:
    parsed = parse_opsx_bug_fix_result(
        "STATUS: PARTIAL_COMPLETE\nREPAIRED_THIS_BATCH: NO\n"
    )
    evidence = RepairProgressEvidence(
        product_mutation=False,
        closed_fixed_count=0,
        deferred_product_bug_count=0,
    )
    reconciled = reconcile_repaired_this_batch(parsed, evidence)
    assert reconciled.repaired_this_batch is False


def test_e_loop_re_review_after_durable_repair(monkeypatch) -> None:
    def _force_durable_progress(parsed: RepairRunResult, repo_path, **kwargs):
        parsed.repaired_this_batch = True
        return parsed

    monkeypatch.setattr(
        "loop_engine.apply_durable_repair_progress",
        _force_durable_progress,
    )
    outcome = ReviewRepairLoop(
        MockCursorAdapter(build_scenario("project-bug-hunt-scenario1")),
        lane=PROJECT_BUG_HUNT_LANE,
        repo_path=None,
    ).run()
    assert outcome.status != "NO_REPAIR_PROGRESS"
    assert outcome.repair_rounds >= 1
    assert outcome.review_runs >= 2


def test_f_stale_prose_cannot_override_durable_receipts(tmp_path: Path) -> None:
    materialize = tmp_path / "opsx/runs/bug-cases/materialize"
    materialize.mkdir(parents=True)
    (materialize / "closed-WI-TEST-9.json").write_text(
        json.dumps({"terminal_status": "closed_fixed"}),
        encoding="utf-8",
    )
    parsed = parse_opsx_bug_fix_result(
        "STATUS: BUG_BATCH_CLEAR\n"
        "BUG_BATCH_CLEAR: YES\n"
        "REPAIRED_THIS_BATCH: NO\n"
        "OWNER_AGGREGATE:\n"
        "  FIX_NOW: (none)\n"
    )
    assert parsed.repaired_this_batch is False
    since = time.monotonic() - 10
    parsed = apply_durable_repair_progress(
        parsed,
        tmp_path,
        before_fingerprint="",
        since_monotonic=since,
    )
    assert parsed.repaired_this_batch is True
