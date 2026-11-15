"""Evidence-only ``resolve_row_work``: spec section, test file, walk vs batch."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.verify.scenario_coverage_verify import row_receipts
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
from agents.verify.scenario_coverage_verify.row_disposition import (
    build_scenario_coverage_row_context,
    resolve_row_work,
)
from agents.common.scenario_receipt_test_paths import plan_receipt_path, trace_matrix_path
from agents.verify.scenario_coverage_verify.row_receipts import (
    save_supported_row,
    write_observation,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageRowReview,
)
from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT

# Фикстуры plan A/B — те же, что в test_scenario_delta_rows.
from .test_scenario_delta_rows import (  # noqa: PLC2701
    _demo,
    _paths,
    _real_scenario_fixture,
)


def _ctx(root: Path, authority: dict):
    return build_scenario_coverage_row_context(root, "demo", authority=authority)


def _patch_proofs(monkeypatch):
    monkeypatch.setattr(row_receipts, "review_proof_error", lambda *_a, **_k: None)


def _row(sid: str, node: str) -> ScenarioCoverageRowReview:
    return ScenarioCoverageRowReview(
        scenario_id=sid,
        test_node_id=node,
        assessment="SUPPORTED",
        evidence="checked",
        when_proofs=[1],
        outcome_proofs=[2],
    )


def _save_pass(root, mapping, authority, ids):
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    for sid in ids:
        save_supported_row(
            root,
            change_id="demo",
            row=_row(sid, mapping[sid]),
            mapping=mapping,
            row_digest=scope["row_digests"][sid],
            plan_evidence_id=authority["plan_evidence_id"],
            origin_execution_id="exec-1",
            evidence_id=f"run:{sid}",
            row_identity_contract=ROW_CONTRACT,
        )


def test_a1_both_supported_no_cycle(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    ctx = _ctx(root, authority)
    for sid in ids:
        work = resolve_row_work(ctx, sid, disposition_mode="walk")
        assert not work.run_check_repair_cycle
        assert work.reason == "FRESH_SUPPORTED_RECEIPT"


def test_a2_edit_scenario_a_only_invalidates_a(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("outcome A", "changed outcome A"),
        encoding="utf-8",
    )
    ctx = _ctx(root, authority)
    wa = resolve_row_work(ctx, ids[0], disposition_mode="walk")
    wb = resolve_row_work(ctx, ids[1], disposition_mode="walk")
    assert wa.run_check_repair_cycle
    assert not wb.run_check_repair_cycle


def test_a3_edit_scenario_b_only(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("outcome B", "changed outcome B"),
        encoding="utf-8",
    )
    ctx = _ctx(root, authority)
    assert not resolve_row_work(ctx, ids[0], disposition_mode="walk").run_check_repair_cycle
    assert resolve_row_work(ctx, ids[1], disposition_mode="walk").run_check_repair_cycle


def test_a4_preamble_spec_does_not_invalidate(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text("Intro\n" + spec.read_text(encoding="utf-8"), encoding="utf-8")
    ctx = _ctx(root, authority)
    for sid in ids:
        assert not resolve_row_work(ctx, sid, disposition_mode="walk").run_check_repair_cycle


def test_a5_row_digests_differ_for_siblings(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    ctx = _ctx(root, authority)
    assert ctx.row_digests[ids[0]] != ctx.row_digests[ids[1]]


def test_b1_edit_test_b_only(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    (root / "tests/test_b.py").write_text("def test_b():\n    assert 9\n", encoding="utf-8")
    ctx = _ctx(root, authority)
    assert not resolve_row_work(ctx, ids[0], disposition_mode="walk").run_check_repair_cycle
    assert resolve_row_work(ctx, ids[1], disposition_mode="walk").run_check_repair_cycle


def test_b2_shared_test_module_invalidates_both(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    mapping[ids[1]] = "tests/test_a.py::test_b"
    matrix = trace_matrix_path(root / "openspec/changes/demo/scenario-coverage")
    matrix.write_text(json.dumps({
        "entries": [
            {"scenario_id": sid, "test_node_id": node}
            for sid, node in mapping.items()
        ],
    }), encoding="utf-8")
    (root / "tests/test_a.py").write_text(
        "def test_a():\n    assert 1\n\ndef test_b():\n    assert 2\n",
        encoding="utf-8",
    )
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    (root / "tests/test_a.py").write_text(
        "def test_a():\n    assert 3\n\ndef test_b():\n    assert 4\n",
        encoding="utf-8",
    )
    ctx = _ctx(root, authority)
    for sid in ids:
        assert resolve_row_work(ctx, sid, disposition_mode="walk").run_check_repair_cycle


def test_b3_missing_test_file(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    (root / "tests/test_a.py").unlink()
    ctx = _ctx(root, authority)
    work = resolve_row_work(ctx, ids[0], disposition_mode="walk")
    assert work.run_check_repair_cycle
    assert work.reason == "MAPPED_TEST_MISSING"


def test_c1_gap_batch_reuse_walk_cycles(tmp_path, monkeypatch):
    root, mapping, authority = _demo(tmp_path)
    _patch_proofs(monkeypatch)
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    sid = "a"
    row = ScenarioCoverageRowReview(
        scenario_id=sid,
        test_node_id=mapping[sid],
        assessment="GAP",
        evidence="gap",
        when_proofs=[],
        outcome_proofs=[],
    )
    from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot

    snapshot = input_snapshot(root, "demo")
    write_observation(
        root,
        change_id="demo",
        row=row,
        row_digest=scope["row_digests"][sid],
        source_digest=snapshot["source_digest"],
        plan_evidence_id=authority["plan_evidence_id"],
        origin_execution_id="exec-1",
        evidence_id="run:gap",
        shared_digest=scope["shared_digest"],
        row_identity_contract=ROW_CONTRACT,
        gaps=[ScenarioCoverageGap(
            scenario_id=sid,
            gap_class="MISSING_TEST",
            evidence="no assert",
        )],
    )
    ctx = _ctx(root, authority)
    batch = resolve_row_work(ctx, sid, disposition_mode="batch")
    walk = resolve_row_work(ctx, sid, disposition_mode="walk")
    assert not batch.run_check_repair_cycle
    assert batch.reason == "DURABLE_GAP_REUSE_BATCH"
    assert walk.run_check_repair_cycle


def test_c2_supported_both_modes(tmp_path, monkeypatch):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _patch_proofs(monkeypatch)
    _save_pass(root, mapping, authority, ids)
    ctx = _ctx(root, authority)
    for sid in ids:
        for mode in ("batch", "walk"):
            assert not resolve_row_work(ctx, sid, disposition_mode=mode).run_check_repair_cycle


def test_c3_no_receipt_needs_cycle(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    ctx = _ctx(root, authority)
    work = resolve_row_work(ctx, ids[0], disposition_mode="walk")
    assert work.run_check_repair_cycle
    assert work.reason == "NO_SUPPORTED_RECEIPT"
