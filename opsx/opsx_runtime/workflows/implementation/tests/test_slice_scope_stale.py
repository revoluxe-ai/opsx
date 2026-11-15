"""SCOPE_STALE: изменение work file без обновления digest."""

from __future__ import annotations

import json
from pathlib import Path

from workflows.implementation.routing_contract import STAGE_APPLY_RED
from workflows.implementation.slice_check_workflow_receipt import (
    WORK_SCOPE_DIGEST_KEY,
    evaluate_slice_stage,
)


def test_scope_stale_when_work_file_changes(tmp_path: Path) -> None:
    change = "stale-ch"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "tests/stale_test.py"
    test_file = repo / rel
    test_file.parent.mkdir(parents=True)
    _ = test_file.write_text("v1", encoding="utf-8")
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    receipt_path = red_dir / "RED.json"
    _ = receipt_path.write_text(
        json.dumps({"status": "RED_PROVEN", "test_paths": [rel], "fixture_paths": []}),
        encoding="utf-8",
    )
    first = evaluate_slice_stage(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert first.valid, first.code
    from workflows.implementation.slice_receipt import load_stage_receipt

    merged = load_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert merged is not None
    assert merged.get(WORK_SCOPE_DIGEST_KEY)
    _ = test_file.write_text("v2", encoding="utf-8")
    second = evaluate_slice_stage(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert not second.valid
    assert second.code == "SCOPE_STALE"
