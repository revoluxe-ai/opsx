"""Детерминированные slice receipt: минимальные пути и отпечатки."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workflows.implementation.routing_contract import (
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt import (
    validate_stage_receipt,
    work_paths_from_receipt,
    write_work_fingerprints,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_work_paths_scaffold_minimal() -> None:
    receipt = {
        "created_paths": [],
        "extended_paths": ["src/a.py"],
        "authorized_write_paths": ["src/b.py"],
    }
    assert work_paths_from_receipt(STAGE_APPLY_SCAFFOLD, receipt) == ["src/a.py"]


def test_validate_requires_fingerprints_not_json_metadata(tmp_path: Path) -> None:
    change = "ch"
    slice_id = "SLICE-001"
    repo = tmp_path
    root = repo / "openspec" / "changes" / change
    scaffold_dir = root / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    rel = "src/foo.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_text("hello", encoding="utf-8")
    receipt_path = scaffold_dir / "SCAFFOLD.json"
    receipt = {
        "status": "SCAFFOLD_READY",
        "created_paths": [],
        "extended_paths": [rel],
        "generated_at": "2099-01-01",
    }
    _ = receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    first = validate_stage_receipt(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    assert first.valid, first.code
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk.get("work_scope_digest")
    _ = write_work_fingerprints(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    second = validate_stage_receipt(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    assert second.valid
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    on_disk["generated_at"] = "2099-02-02"
    _ = receipt_path.write_text(json.dumps(on_disk), encoding="utf-8")
    third = validate_stage_receipt(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    assert third.valid


def test_validate_same_answer_twice(tmp_path: Path) -> None:
    change = "ch2"
    slice_id = "SLICE-001"
    repo = tmp_path
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    rel = "tests/x_test.py"
    test_file = repo / rel
    test_file.parent.mkdir(parents=True)
    _ = test_file.write_text("def test_x(): pass", encoding="utf-8")
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": [rel],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(receipt), encoding="utf-8")
    a = validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    b = validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert a == b
    assert a.valid
