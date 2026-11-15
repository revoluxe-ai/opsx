"""Preserve scenario-coverage plan при неизменном design."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents.common.scenario_files import file_sha256, plan_content_hash
from agents.planning.scenario_coverage_plan.receipt import PreserveAction, ScenarioCoveragePlanReceipt
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    row_journal_path,
    trace_matrix_path,
)


def _minimal_change(root: Path, *, design_text: str = "design\n") -> Path:
    change = root / "openspec" / "changes" / "demo"
    change.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("proposal.md", "p\n"),
        ("design.md", design_text),
        ("tasks.md", "t\n"),
    ):
        (change / name).write_text(body, encoding="utf-8")
    spec = change / "specs" / "demo" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("spec\n", encoding="utf-8")
    coverage = change / "scenario-coverage"
    coverage.mkdir(parents=True)
    ensure_machine_receipt_dirs(coverage)
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    scenario_path = coverage / "scenarios" / (
        hashlib.sha256(sid.encode()).hexdigest() + ".md"
    )
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_body = f"- SCENARIO_ID: {sid}\n"
    scenario_path.write_text(scenario_body, encoding="utf-8")
    plan_text = "# plan\n"
    (coverage / "plan.md").write_text(plan_text, encoding="utf-8")
    plan_hash = plan_content_hash(
        plan_text.encode(),
        [(sid, file_sha256(scenario_body.encode()))],
    )
    plan_receipt_path(coverage).write_text(
        json.dumps(
            {
                "SCENARIO_COVERAGE_RECEIPT": True,
                "CHANGE_ID": "demo",
                "PLAN_STATUS": "ready",
                "PLAN_EVIDENCE_ID": "p1",
                "PLAN_CONTENT_HASH": plan_hash,
                "SCENARIO_FILES": [
                    {
                        "scenario_id": sid,
                        "path": f"scenarios/{scenario_path.name}",
                        "content_hash": hashlib.sha256(scenario_body.encode()).hexdigest(),
                    }
                ],
                "INPUTS": {
                    "DESIGN_REVISION": {
                        "path": "design.md",
                        "content_hash": hashlib.sha256(design_text.encode()).hexdigest(),
                    },
                    "SPEC_REVISION": [
                        {
                            "path": "specs/demo/spec.md",
                            "content_hash": hashlib.sha256(b"spec\n").hexdigest(),
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return change


def test_preserve_when_design_matches(tmp_path: Path) -> None:
    _minimal_change(tmp_path)
    receipt = ScenarioCoveragePlanReceipt(tmp_path, "demo")
    assert receipt.preserve_decision().action == PreserveAction.PRESERVED


def test_needs_agent_when_design_changes(tmp_path: Path) -> None:
    _minimal_change(tmp_path, design_text="design\n")
    (tmp_path / "openspec/changes/demo/design.md").write_text("design v2\n", encoding="utf-8")
    receipt = ScenarioCoveragePlanReceipt(tmp_path, "demo")
    assert receipt.preserve_decision().action == PreserveAction.NEEDS_AGENT
