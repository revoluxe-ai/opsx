"""Тесты сверки recipe pins (CRLF, пробелы, JSON) через CheckWorkflowReceipt."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.utils.receipt_file_identity import file_pin_matches, sha256_bytes
from check_workflow_receipt import CheckWorkflowReceipt, CheckWorkflowReceiptStatus
from workflows.verify.cycle_checks import CheckName
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)


def test_crlf_disk_matches_lf_pin(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    lf_pin = sha256_bytes(b"design.md\n")
    design.write_bytes(b"design.md\r\n")
    assert CheckWorkflowReceipt.matches_file(design, lf_pin)


def test_lf_disk_matches_crlf_pin(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    crlf_pin = sha256_bytes(b"line\r\n")
    spec.write_bytes(b"line\n")
    assert CheckWorkflowReceipt.matches_file(spec, crlf_pin)


def test_whitespace_noise_matches_canonical_pin(tmp_path: Path) -> None:
    clean = b"hello world\n"
    pin = sha256_bytes(clean)
    noisy = tmp_path / "f.md"
    noisy.write_bytes(b"hello  world  \r\n")
    assert file_pin_matches(noisy.read_bytes(), pin)


def test_agent_skip_hop_when_pins_match(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    pin = sha256_bytes(b"stable\n")
    design.write_bytes(b"stable\r\n")
    receipt = CheckWorkflowReceipt(pinned=((design, pin),))
    assert receipt.agent_skip_hop({}) is True
    design.write_text("changed\n", encoding="utf-8")
    assert receipt.agent_skip_hop({}) is False


def test_text_change_does_not_match(tmp_path: Path) -> None:
    path = tmp_path / "owner-contract.md"
    pin = sha256_bytes(b"opsx/intents/\n")
    path.write_bytes(b".opsx/intents/\n")
    assert not CheckWorkflowReceipt.matches_file(path, pin)


def test_workflow_receipt_crlf_scope_match(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    app = tmp_path / "src" / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    receipt = CheckWorkflowReceipt(CheckName.PROJECT_BUG, ("src",))
    state = {"repository_path": str(tmp_path), "change_id": "demo"}
    patch = receipt.establish_patch(state)
    merged = {**state, **patch}
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.MATCH
    app.write_bytes(b"x = 1\r\n")
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.MATCH
    app.write_text("x = 2\n", encoding="utf-8")
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.STALE


def test_inspect_plan_inputs_not_stale_on_crlf_design(tmp_path: Path) -> None:
    change = tmp_path / "openspec" / "changes" / "demo"
    change.mkdir(parents=True)
    for name in ("proposal.md", "design.md", "tasks.md"):
        (change / name).write_text(name + "\n", encoding="utf-8")
    spec = change / "specs" / "demo" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("#### Scenario: one\n- **WHEN** a\n", encoding="utf-8")
    coverage = change / "scenario-coverage"
    coverage.mkdir()
    ensure_machine_receipt_dirs(coverage)
    design_lf = (change / "design.md").read_bytes()
    pin = hashlib.sha256(design_lf).hexdigest()
    (change / "design.md").write_bytes(design_lf.replace(b"\n", b"\r\n"))
    (coverage / "plan.md").write_text("CHANGE_ID: demo\n", encoding="utf-8")
    import json

    plan_receipt_path(coverage).write_text(
        json.dumps(
            {
                "SCENARIO_COVERAGE_RECEIPT": True,
                "CHANGE_ID": "demo",
                "PLAN_STATUS": "ready",
                "PLAN_EVIDENCE_ID": "plan-1",
                "PLAN_CONTENT_HASH": "0" * 64,
                "INPUTS": {
                    "DESIGN_REVISION": {"path": "design.md", "content_hash": pin},
                    "SPEC_REVISION": [
                        {
                            "path": "specs/demo/spec.md",
                            "content_hash": hashlib.sha256(spec.read_bytes()).hexdigest(),
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (coverage / "scenarios").mkdir()
    trace_matrix_path(coverage).write_text("{}", encoding="utf-8")
    status = inspect_plan_artifacts(tmp_path, "demo", phase="planning")
    assert status["code"] != "COVERAGE_INPUT_STALE", status
