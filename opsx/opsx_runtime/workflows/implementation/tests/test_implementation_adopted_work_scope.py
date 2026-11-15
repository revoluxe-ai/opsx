"""IMPLEMENTATION_ADOPTED: пустой product scope — не STATUS_ONLY_STUB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows.implementation.routing_contract import STAGE_APPLY_IMPLEMENT
from workflows.implementation.slice_check_workflow_receipt import (
    evaluate_slice_stage,
    persist_after_successful_hop,
)


@pytest.mark.unit
def test_implementation_adopted_empty_product_paths_accepted(tmp_path: Path) -> None:
    change = "visual-adopt-gate"
    slice_id = "SLICE-001"
    repo = tmp_path
    impl_dir = repo / "openspec" / "changes" / change / "apply-implement" / slice_id
    impl_dir.mkdir(parents=True)
    receipt = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "change_id": change,
        "slice_id": slice_id,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "production_paths_modified": [],
        "production_paths_created": [],
        "product_files_modified": False,
    }
    _ = (impl_dir / "IMPLEMENTATION.json").write_text(json.dumps(receipt), encoding="utf-8")
    pre = evaluate_slice_stage(change, slice_id, STAGE_APPLY_IMPLEMENT, repo_root=repo)
    assert pre.valid, pre.code
    assert pre.code is None
    post = persist_after_successful_hop(
        change,
        slice_id,
        STAGE_APPLY_IMPLEMENT,
        repo_root=repo,
    )
    assert post.valid, post.code
