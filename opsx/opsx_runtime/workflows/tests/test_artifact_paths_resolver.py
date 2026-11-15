"""Резолвер путей receipts: канонический корень opsx/receipts."""

from __future__ import annotations

from pathlib import Path

from workflows.artifact_paths import (
    apply_design_artifact_path,
    apply_design_hop_receipt_path,
    receipts_dir,
    stage_receipt_path,
)


def test_receipts_dir_under_opsx(tmp_path: Path) -> None:
    change = "visual"
    root = receipts_dir(tmp_path, change)
    assert root == tmp_path / "opsx" / "receipts" / change


def test_stage_receipt_path_applies_to_opsx_receipts(tmp_path: Path) -> None:
    path = stage_receipt_path(tmp_path, "visual", "APPLY_GREEN", "SLICE-001")
    assert path.parts[-3:] == ("apply-green", "SLICE-001", "GREEN.json")
    assert "opsx" in path.parts and "receipts" in path.parts
    assert path.name == "GREEN.json"


def test_apply_design_artifact_path_is_openspec(tmp_path: Path) -> None:
    path = apply_design_artifact_path(tmp_path, "visual")
    assert path.name == "APPLY-DESIGN.json"
    assert "openspec" in path.parts and "changes" in path.parts
    assert "receipts" not in path.parts
    receipt = apply_design_hop_receipt_path(tmp_path, "visual")
    assert receipt.name == "hop.receipt.json"
    assert "receipts" in receipt.parts
