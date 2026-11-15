"""Hermetic PBH hunt → markdown orch repair → materialize → re-hunt on new HEAD."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from commit_materialization import current_head
from test_commit_materialization import _fix_product, _hunt_clean, _hunt_findings, _run_lane, init_repo
from workflow_repair_transition import repair_activity_allows_commit_materialize

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "markdown_orch_bug_batch_clear_incident.txt"
)


def _markdown_repair_yes() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_hermetic_markdown_repair_materialize_then_rehunt_on_new_head(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo,
        lane_id="project-bug-hunt",
        mutate=_fix_product,
        sha_a=sha_a,
        repair_output=_markdown_repair_yes(),
        review_outputs=[_hunt_findings(), _hunt_clean()],
    )
    sha_b = current_head(repo)
    assert sha_b and sha_b != sha_a
    assert outcome.status == "HUNT_CLEAN"
    assert outcome.review_runs == 2
    assert outcome.repair_rounds == 1
    assert outcome.last_materialize is not None
    assert outcome.last_materialize["ok"] is True
    assert outcome.last_materialize["head_before"] == sha_a
    assert outcome.last_materialize["head_after"] == sha_b
    assert adapter.review_heads[0] == sha_a
    assert adapter.review_heads[-1] == sha_b
    assert adapter.steps == ["review", "repair", "commit-all", "review"]
    assert outcome.last_repair is not None
    assert repair_activity_allows_commit_materialize(outcome.last_repair.to_dict()) is True


def test_hermetic_markdown_partial_complete_skips_materialize(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo,
        lane_id="project-bug-hunt",
        mutate=_fix_product,
        sha_a=sha_a,
        repair_output=(
            "**STATUS:** PARTIAL_COMPLETE\n"
            "**REPAIRED_THIS_BATCH:** NO\n"
            "BUG_BATCH_CLEAR: NO\n"
            "OWNER_AGGREGATE:\n  FIX_NOW: (none)\n"
        ),
        review_outputs=[_hunt_findings()],
    )
    assert outcome.status == "PARTIAL_COMPLETE"
    assert outcome.last_materialize is None
    assert adapter.commit_all_calls == 0
    assert current_head(repo) == sha_a


def test_hermetic_garbled_markdown_status_stays_uncertain(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo,
        lane_id="project-bug-hunt",
        mutate=_fix_product,
        sha_a=sha_a,
        repair_output="**REPAIRED_THIS_BATCH:** YES\n(no STATUS line)\n",
        review_outputs=[_hunt_findings()],
    )
    assert outcome.status == "REPAIR_COMPLETION_UNCERTAIN"
    assert outcome.last_materialize is None
    assert adapter.commit_all_calls == 0
