"""Read-only view: no shared registry, no false-green status from observations."""
from __future__ import annotations

import json

from agents.verify.scenario_coverage_verify import row_receipts, row_status
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    source_paths,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from agents.verify.scenario_coverage_verify.row_status import inspect_rows
from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageRowReview

_MODEL = {"id": "composer-2.5", "options": []}


def _demo(root, monkeypatch):
    monkeypatch.setattr(row_status, "scenario_verifier_contract_digest",
                        lambda *args, **kwargs: "policy-v1")
    plan = root / "openspec/changes/demo/scenario-coverage"
    plan.mkdir(parents=True)
    ensure_machine_receipt_dirs(plan)
    ids = ["a", "b", "c"]
    mapping = {sid: f"tests/test_{sid}.py::test_{sid}" for sid in ids}
    (plan / "plan.md").write_text(
        "".join(f"- SCENARIO_ID: {sid}\n" for sid in ids), encoding="utf-8",
    )
    plan_receipt_path(plan).write_text(
        '{"PLAN_EVIDENCE_ID":"frozen"}', encoding="utf-8",
    )
    trace_matrix_path(plan).write_text(json.dumps({
        "entries": [{"scenario_id": sid, "test_node_id": mapping[sid]} for sid in ids],
    }), encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    for sid in ids:
        (tests / f"test_{sid}.py").write_text(
            f"def test_{sid}():\n    assert True\n", encoding="utf-8",
        )
    monkeypatch.setattr(row_receipts, "review_proof_error", lambda *a, **k: None)
    scope = impact_scope(
        root, source_paths(root, "demo"), mapping,
        authority={"model": _MODEL, "plan_evidence_id": "frozen",
                   "verifier_contract_digest": "policy-v1"},
    )
    digest = input_snapshot(root, "demo")["source_digest"]
    for sid, assessment in (("a", "SUPPORTED"), ("b", "GAP"), ("c", "INSPECTION_BLOCKED")):
        row = ScenarioCoverageRowReview(
            scenario_id=sid, assessment=assessment, test_node_id=mapping[sid],
            evidence=f"source evidence for {sid}",
            when_proofs=[1] if assessment == "SUPPORTED" else [],
            outcome_proofs=[2] if assessment == "SUPPORTED" else [],
        )
        row_receipts.write_observation(
            root, change_id="demo", row=row,
            source_digest=digest, row_digest=scope["row_digests"][sid],
            plan_evidence_id="frozen", origin_execution_id="exec-1",
            evidence_id=f"run:{sid}",
            problem="THEN assertion missing" if sid == "b" else "Cannot inspect" if sid == "c" else "",
            gap_class="WEAK_TEST" if sid == "b" else "",
        )
        if sid == "a":
            assert row_receipts.save_supported_row(
                root, change_id="demo", row=row, mapping=mapping,
                row_digest=scope["row_digests"][sid], plan_evidence_id="frozen",
                origin_execution_id="exec-1", evidence_id=f"run:{sid}",
            )
    return mapping


def test_readonly_script_reports_exact_pass_fail_pause_with_reason(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    rows = inspect_rows(tmp_path, "demo", model=_MODEL)
    assert [(r["scenario_id"], r["result"]) for r in rows] == [
        ("a", "PASS"), ("b", "FAIL"), ("c", "PAUSED"),
    ]
    assert rows[1]["gap_class"] == "WEAK_TEST"
    assert rows[1]["problem"] == "THEN assertion missing"
    assert rows[2]["problem"] == "Cannot inspect"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_stale_negative_observation_never_claims_current_fail(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    (tmp_path / "tests/test_b.py").write_text(
        "def test_b():\n    assert False\n", encoding="utf-8",
    )
    result = {r["scenario_id"]: r for r in inspect_rows(tmp_path, "demo", model=_MODEL)}
    assert result["a"]["result"] == "PASS"  # isolated sibling test unchanged
    assert result["b"]["result"] == "STALE"
    assert result["c"]["result"] == "STALE"


def test_observation_supported_cannot_claim_pass_without_valid_receipt(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    pass_file = next((tmp_path / "opsx/receipts/demo/check-receipts").glob(
        "scenario_supported_*.json"
    ))
    pass_file.unlink()
    rows = {r["scenario_id"]: r for r in inspect_rows(tmp_path, "demo", model=_MODEL)}
    assert rows["a"]["result"] == "UNVERIFIED"


def test_untracked_extra_receipts_cannot_expand_frozen_plan(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    path = tmp_path / "opsx/receipts/demo/scenario-rows/extraneous.json"
    path.write_text('{"result":"PASS"}', encoding="utf-8")
    rows = inspect_rows(tmp_path, "demo", model=_MODEL)
    assert len(rows) == 3
    assert [r["scenario_id"] for r in rows] == ["a", "b", "c"]

def test_status_policy_identity_change_forces_receipt_miss(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(row_status, "scenario_verifier_contract_digest",
                        lambda *args, **kwargs: "different-verifier-policy")
    rows = {r["scenario_id"]: r for r in inspect_rows(tmp_path, "demo", model=_MODEL)}
    assert rows["a"]["result"] == "UNVERIFIED"
    assert rows["b"]["result"] == "FAIL"  # negative observation stays diagnostic only


def test_status_model_change_forces_receipt_miss(tmp_path, monkeypatch):
    _demo(tmp_path, monkeypatch)
    changed_model = {"id": "different-tool-tier", "options": []}
    rows = {r["scenario_id"]: r for r in inspect_rows(
        tmp_path, "demo", model=changed_model
    )}
    assert rows["a"]["result"] == "UNVERIFIED"


def test_status_does_not_count_multiple_gaps_as_pass(tmp_path, monkeypatch):
    mapping = _demo(tmp_path, monkeypatch)
    from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageGap
    snapshot = input_snapshot(tmp_path, "demo")["source_digest"]
    scope = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping,
        authority={"model": _MODEL, "plan_evidence_id": "frozen",
                   "verifier_contract_digest": "policy-v1"},
    )
    row = ScenarioCoverageRowReview(
        scenario_id="b", test_node_id=mapping["b"], assessment="GAP", evidence="two gaps",
    )
    row_receipts.write_observation(
        tmp_path, change_id="demo", row=row,
        row_digest=scope["row_digests"]["b"], source_digest=snapshot,
        plan_evidence_id="frozen", origin_execution_id="exec-2",
        evidence_id="sdk-2:agent-2", gaps=[
            ScenarioCoverageGap(scenario_id="b", gap_class="NO_WHEN", evidence="missing setup"),
            ScenarioCoverageGap(scenario_id="b", gap_class="NO_THEN", evidence="missing assert"),
        ],
    )
    assert len(row_receipts.read_observation(
        tmp_path, change_id="demo", scenario_id="b",
    )["gaps"]) == 2
    statuses = {r["scenario_id"]: r for r in inspect_rows(tmp_path, "demo", model=_MODEL)}
    assert statuses["b"]["result"] == "FAIL"
