"""Subprocess RED operative gate (вне SDK shell)."""

from __future__ import annotations

from pathlib import Path

from workflows.implementation.red_operative_validation import (
    slice_red_json_path,
    validate_red_operative,
)
from workflows.repo_paths import resolve_opsx_repository_root


def test_validate_visual_red_returns_structured_result() -> None:
    """Машинная проверка visual SLICE-001 возвращает PASS или осмысленный FAIL."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    red_path = slice_red_json_path("visual", repo_root=repo, slice_id="SLICE-001")
    gate = validate_red_operative(red_path, repo_root=repo, timeout_sec=180)
    assert gate.red_json_path.is_file()
    assert gate.status in {"PASS", "FAIL", "MISSING"}
    if gate.ok:
        assert gate.exit_code == 0
        assert gate.status == "PASS"
    else:
        assert gate.errors or gate.exit_code != 0
