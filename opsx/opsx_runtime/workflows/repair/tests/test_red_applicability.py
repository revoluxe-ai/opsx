"""RED applicability structural gates (effective profile / in-test preview)."""

from __future__ import annotations

from pathlib import Path

from workflows.repair.red_requirement_applicability import (
    build_red_applicability_pack,
    evaluate_red_applicability,
)
from workflows.repair.schemas import RedEvidence


def test_in_test_preview_mismatch_blocks_applicability(tmp_path: Path) -> None:
    tests = tmp_path / "tests" / "unit"
    tests.mkdir(parents=True)
    test_file = tests / "test_x.py"
    test_file.write_text(
        'def test_x(monkeypatch):\n    monkeypatch.setenv("INVEST_YALTA_PREVIEW_UX_UNBLOCK", "1")\n',
        encoding="utf-8",
    )
    rel = test_file.relative_to(tmp_path)
    node_id = f"{rel.as_posix()}::test_x"
    evidence = RedEvidence.model_validate(
        {
            "status": "RED_CONFIRMED",
            "test_id": node_id,
            "command": "pytest",
            "expected_failure": "AssertionError",
            "actual_failure": "AssertionError",
            "failure_match": True,
        },
    )
    pack = build_red_applicability_pack(
        evidence=evidence,
        repo_root=tmp_path,
        test_content_identity="deadbeef",
        pytest_result={"exit_code": 1},
        requirement_fingerprint="F-1",
        obligation_harness_profile="production_like",
    )
    result = evaluate_red_applicability(pack)
    assert not result.ok
    assert "RED_OPERATIVE_EFFECTIVE_PROFILE_MISMATCH" in result.errors


def test_structural_pass_without_preview_override(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_plain.py"
    test_file.write_text("def test_plain():\n    assert False\n", encoding="utf-8")
    node_id = "tests/test_plain.py::test_plain"
    evidence = RedEvidence.model_validate(
        {
            "status": "RED_CONFIRMED",
            "test_id": node_id,
            "command": "pytest",
            "expected_failure": "AssertionError",
            "actual_failure": "AssertionError",
            "failure_match": True,
        },
    )
    pack = build_red_applicability_pack(
        evidence=evidence,
        repo_root=tmp_path,
        test_content_identity="abc",
        pytest_result={"exit_code": 1},
        requirement_fingerprint="F-2",
    )
    result = evaluate_red_applicability(pack)
    assert result.ok
