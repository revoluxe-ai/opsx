"""Operative gate Bug Team RED_TEST_WORKER: live pytest без хэшей от модели."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from workflows.repair.checks import red_green_identity_errors
from workflows.repair.red_operative_validation import (
    RedOperativeValidation,
    red_test_file_content_identity,
    validate_bug_team_red_operative,
)
from workflows.repair.schemas import GreenEvidence, RedEvidence


def _red_payload(**extra: object) -> RedEvidence:
    base = {
        "status": "RED_CONFIRMED",
        "test_id": "tests/unit/foo.py::test_bar",
        "command": "pytest tests/unit/foo.py::test_bar",
        "expected_failure": "AssertionError: expected x",
        "actual_failure": "AssertionError: expected x, got y",
        "failure_match": True,
    }
    base.update(extra)
    return RedEvidence.model_validate(base)


def test_red_operative_accepts_failing_pytest_without_model_hashes(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "unit" / "foo.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_bar():\n    assert False, 'expected x, got y'\n", encoding="utf-8")
    evidence = _red_payload(
        actual_failure="AssertionError: expected x, got y",
    )
    with patch(
        "workflows.repair.red_operative_validation.run_repair_hop_pytest_sync",
        return_value={
            "ok": False,
            "exit_code": 1,
            "node_id": evidence.test_id,
            "tail": "FAILED ... AssertionError: expected x, got y",
            "timed_out": False,
        },
    ):
        gate = validate_bug_team_red_operative(evidence, repo_root=tmp_path)
    assert gate.ok is True
    assert gate.test_content_identity == red_test_file_content_identity(
        tmp_path,
        evidence.test_id,
    )


def test_red_operative_rejects_passing_pytest(tmp_path: Path) -> None:
    evidence = _red_payload()
    with patch(
        "workflows.repair.red_operative_validation.run_repair_hop_pytest_sync",
        return_value={
            "ok": True,
            "exit_code": 0,
            "node_id": evidence.test_id,
            "tail": "1 passed",
            "timed_out": False,
        },
    ):
        gate = validate_bug_team_red_operative(evidence, repo_root=tmp_path)
    assert gate.ok is False
    assert "RED_OPERATIVE_TEST_PASSED_NOT_RED" in gate.errors


def test_red_operative_rejects_tool_error(tmp_path: Path) -> None:
    evidence = _red_payload()
    with patch(
        "workflows.repair.red_operative_validation.run_repair_hop_pytest_sync",
        return_value={
            "ok": False,
            "code": "INVALID_NODE_ID",
            "error": "bad node",
        },
    ):
        gate = validate_bug_team_red_operative(evidence, repo_root=tmp_path)
    assert gate.ok is False
    assert any("INVALID_NODE_ID" in err for err in gate.errors)


def test_red_green_identity_uses_runtime_not_model_fields() -> None:
    red = _red_payload()
    green = GreenEvidence(
        status="GREEN_CONFIRMED",
        test_id=red.test_id,
        command="pytest",
    )
    assert (
        red_green_identity_errors(
            red,
            green,
            red_test_content_identity="aaa",
            current_test_content_identity="aaa",
        )
        == []
    )
    assert red_green_identity_errors(
        red,
        green,
        red_test_content_identity="aaa",
        current_test_content_identity="bbb",
    ) == ["TEST_HASH_CHANGED_WITHOUT_NON_WEAKEN_DELTA"]


def test_non_red_status_skips_operative_pytest(tmp_path: Path) -> None:
    evidence = RedEvidence(status="PASS_ALREADY")
    with patch(
        "workflows.repair.red_operative_validation.run_repair_hop_pytest_sync",
    ) as run_mock:
        gate = validate_bug_team_red_operative(evidence, repo_root=tmp_path)
    run_mock.assert_not_called()
    assert gate == RedOperativeValidation(ok=True, test_content_identity=None, errors=[])
