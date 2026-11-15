"""Verify preflight не блокирует только на missing canonical tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.common.scenario_files import materialize_scenario_files, scenario_digest
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)


def _minimal_verify_tree(root: Path) -> None:
    change = root / "openspec" / "changes" / "demo"
    change.mkdir(parents=True, exist_ok=True)
    for name in ("proposal.md", "design.md", "tasks.md"):
        (change / name).write_text(name + "\n", encoding="utf-8")
    spec = change / "specs" / "demo" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("#### Scenario: one\n", encoding="utf-8")
    coverage = change / "scenario-coverage"
    coverage.mkdir(parents=True)
    ensure_machine_receipt_dirs(coverage)
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    (coverage / "plan.md").write_text("# plan\n", encoding="utf-8")
    digest = scenario_digest(sid)
    scenario_path = coverage / "scenarios" / f"{digest}.md"
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(f"- SCENARIO_ID: {sid}\n", encoding="utf-8")
    scenario_hash = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    plan_receipt_path(coverage).write_text(
        json.dumps(
            {
                "SCENARIO_COVERAGE_RECEIPT": True,
                "CHANGE_ID": "demo",
                "PLAN_STATUS": "ready",
                "PLAN_EVIDENCE_ID": "p1",
                "SCENARIO_FILES": [
                    {
                        "scenario_id": sid,
                        "path": f"scenarios/{digest}.md",
                        "content_hash": scenario_hash,
                    }
                ],
                "INPUTS": {
                    "DESIGN_REVISION": {
                        "path": "design.md",
                        "content_hash": hashlib.sha256(b"design.md\n").hexdigest(),
                    },
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
    from agents.common.scenario_files import expected_plan_content_hash

    materialize_scenario_files(coverage)
    receipt_path = plan_receipt_path(coverage)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["PLAN_CONTENT_HASH"] = expected_plan_content_hash(coverage)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    trace_matrix_path(coverage).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "change_id": "demo",
                "plan_evidence_id": "p1",
                "entries": [{"scenario_id": sid, "test_node_id": ""}],
            }
        ),
        encoding="utf-8",
    )


def test_missing_canonical_test_does_not_block_preflight(tmp_path: Path) -> None:
    _minimal_verify_tree(tmp_path)
    result = inspect_plan_artifacts(tmp_path, "demo", phase="verify")
    assert result["status"] == "READY"
