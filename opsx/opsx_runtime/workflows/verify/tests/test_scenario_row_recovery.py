"""Historical scenario dialogue import: no SDK, independently verified row receipts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.verify.scenario_coverage_verify import row_recovery, row_receipts
from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_observation,
    read_supported_row,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageRowReview


def _assert_recovery_outputs_absent(repo: Path, change_id: str = "demo") -> None:
    """Machine plan/matrix may exist; row recovery must not write verify outputs."""
    base = repo / "opsx/receipts" / change_id
    if not base.is_dir():
        return
    for name in ("check-receipts", "scenario-rows"):
        assert not (base / name).exists(), name


def _workspace(root, monkeypatch):
    change = root / "openspec/changes/demo/scenario-coverage"
    change.mkdir(parents=True)
    ensure_machine_receipt_dirs(change)
    plan_ids = ["a", "b", "c"]
    (change / "plan.md").write_text(
        "".join(f"- SCENARIO_ID: {sid}\n" for sid in plan_ids), encoding="utf-8",
    )
    plan_receipt_path(change).write_text(
        '{"PLAN_EVIDENCE_ID":"frozen"}', encoding="utf-8",
    )
    mapping = {
        sid: f"tests/test_{sid}.py::test_{sid}" for sid in plan_ids
    }
    trace_matrix_path(change).write_text(
        json.dumps({"entries": [
            {"scenario_id": sid, "test_node_id": mapping[sid]}
            for sid in plan_ids
        ]}), encoding="utf-8",
    )
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "app.py").write_text("def f(): return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    for sid in plan_ids:
        (tests / f"test_{sid}.py").write_text(
            f"def test_{sid}():\n    assert True\n", encoding="utf-8",
        )
    # Only for unit test of dialogue parsing and receipt publishing.
    # Separate test below exercises actual anchored Git source attestation.
    monkeypatch.setattr(row_recovery, "prove_recovery_freshness", lambda *args, **kwargs: None)
    monkeypatch.setattr(row_recovery, "scenario_verifier_contract_digest", lambda *args, **kwargs: "policy-v1")
    monkeypatch.setattr(row_receipts, "review_proof_error", lambda *args, **kwargs: None)
    return plan_ids, mapping


def _artifacts(root, mapping, plan_ids, *, statuses=None, finished=True):
    statuses = statuses or ["SUPPORTED", "GAP", "INSPECTION_BLOCKED"]
    snapshot = input_snapshot(root, "demo")["source_digest"]
    rows = []
    for sid, status in zip(plan_ids, statuses):
        rows.append({
            "scenario_id": sid, "test_node_id": mapping[sid],
            "assessment": status, "evidence": f"checked source for {sid}",
            "when_proofs": [1] if status == "SUPPORTED" else [],
            "outcome_proofs": [2] if status == "SUPPORTED" else [],
        })
    reply = {
        "verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "plan_evidence_id": "frozen", "source_digest": snapshot,
        "row_reviews": rows, "gaps": [],
        "summary": "historical mixed audit",
        "block_reason": "",
        "local_hop_copy_block": "historical",
    }
    path = root / "conversation.json"
    path.write_text(json.dumps([{
        "type": "agentConversationTurn", "turn": {"steps": [
            {"type": "assistantMessage", "message": {"text": json.dumps(reply)}}
        ]},
    }]), encoding="utf-8")
    meta_path = root / "meta.json"
    meta_path.write_text(json.dumps({
        "sdk_status": "finished" if finished else "running",
        "agent": "scenario_coverage_verify",
        "change_id": "demo", "run_uuid": "historical-execution",
        "model_id": "composer-2.5",
        "sdk_run_id": "sdk-old", "agent_id": "agent-old",
    }), encoding="utf-8")
    return path, meta_path


def _run(root, artifacts, *, write):
    return row_recovery.recover(
        root=root, change_id="demo", anchor="b" * 40,
        conversation_path=artifacts[0], meta_path=artifacts[1], write=write,
    )


def test_dry_run_does_not_write_one_receipt(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids)
    report = _run(tmp_path, artifacts, write=False)
    assert report["status"] == "DRY_RUN"
    assert report["counts"] == {
        "SUPPORTED": 1, "GAP": 1, "INSPECTION_BLOCKED": 1,
        "CONTRACT_CONFLICT": 0,
    }
    assert report["sdk_calls"] == 0 and report["aggregate_verdict"] == "NOT_ASSERTED"
    _assert_recovery_outputs_absent(tmp_path)


def test_recover_individual_evidence_and_never_pass_the_mixed_batch(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids)
    report = _run(tmp_path, artifacts, write=True)
    assert report["written"] == {"observations": 3, "supported_receipts": 1}
    for sid in ids:
        record = read_observation(tmp_path, change_id="demo", scenario_id=sid)
        assert record is not None
        assert record["origin_execution_id"] == "historical-execution"
        assert record["evidence_id"] == "sdk-old:agent-old"
    from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
    from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
    scope = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping,
        authority={"model": {"id": "composer-2.5", "options": []}, "plan_evidence_id": "frozen", "verifier_contract_digest": "policy-v1"},
    )
    good = read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[0], mapping=mapping,
        row_digest=scope["row_digests"][ids[0]], plan_evidence_id="frozen",
    )
    assert good is not None
    assert good[1]["origin_execution_id"] == "historical-execution"
    for sid in ids[1:]:
        assert read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid, mapping=mapping,
            row_digest=scope["row_digests"][sid], plan_evidence_id="frozen",
        ) is None


@pytest.mark.parametrize("kind", ["running_sdk", "plan_mismatch", "missing_row"])
def test_recovery_fails_closed_without_writing_synthetic_pass(tmp_path, monkeypatch, kind):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids, finished=kind != "running_sdk")
    if kind in ("plan_mismatch", "missing_row"):
        obj = json.loads(artifacts[0].read_text(encoding="utf-8"))
        answer = json.loads(obj[0]["turn"]["steps"][0]["message"]["text"])
        if kind == "plan_mismatch":
            answer["plan_evidence_id"] = "different"
        else:
            answer["row_reviews"].pop()
        obj[0]["turn"]["steps"][0]["message"]["text"] = json.dumps(answer)
        artifacts[0].write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="RECOVERY_"):
        _run(tmp_path, artifacts, write=True)
    _assert_recovery_outputs_absent(tmp_path)


def test_one_unverifiable_historical_supported_row_is_not_a_global_blocker(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(
        tmp_path, mapping, ids, statuses=["SUPPORTED", "SUPPORTED", "GAP"],
    )
    monkeypatch.setattr(
        row_receipts, "review_proof_error",
        lambda root, change, row, **kwargs: "MISSING_ASSERTION"
        if row.scenario_id == ids[0] else None,
    )
    report = _run(tmp_path, artifacts, write=True)
    assert report["status"] == "RECOVERED_WITH_REJECTIONS"
    assert ids[0] in report["rejected_supported_proofs"]
    assert report["written"]["supported_receipts"] == 1
    from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
    from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
    scope = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping,
        authority={"model": {"id": "composer-2.5", "options": []}, "plan_evidence_id": "frozen", "verifier_contract_digest": "policy-v1"},
    )
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[0],
        mapping=mapping, row_digest=scope["row_digests"][ids[0]],
        plan_evidence_id="frozen",
    ) is None
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[1],
        mapping=mapping, row_digest=scope["row_digests"][ids[1]],
        plan_evidence_id="frozen",
    ) is not None


def test_original_git_commit_source_digest_recomputed_exactly(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    # Live input_snapshot excludes tests/opsx even if Git tracks it. Historical
    # replay must hash precisely the same source set, not just the same bytes.
    opsx_tests = tmp_path / "tests/opsx"
    opsx_tests.mkdir()
    (opsx_tests / "test_auxiliary.py").write_text("def test_auxiliary(): pass\n", encoding="utf-8")
    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "add", "."],
        ["git", "commit", "-m", "original audited source"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    actual, files = row_recovery.source_digest_at_git_ref(tmp_path, sha, "demo")
    # Pinned Git recovery remains v2; the active verifier snapshot is v3.
    from agents.verify.scenario_coverage_verify.evidence_contract import legacy_source_digest
    expected = legacy_source_digest(tmp_path, "demo")
    assert actual == expected
    assert any(p.startswith("tests/") for p in files)
    assert "tests/opsx/test_auxiliary.py" not in files
    assert any(p.endswith("scenario-coverage/plan.md") for p in files)

def _single_artifacts(root, mapping, ids, *, assessment="GAP", gap_count=2):
    """Build one genuine modern SDK row, preserving multiple independent findings."""
    path, meta = _artifacts(root, mapping, ids)
    obj = json.loads(path.read_text(encoding="utf-8"))
    answer = json.loads(obj[0]["turn"]["steps"][0]["message"]["text"])
    row = answer["row_reviews"][0]
    row["assessment"] = assessment
    row["when_proofs"] = [1] if assessment == "SUPPORTED" else []
    row["outcome_proofs"] = [2] if assessment == "SUPPORTED" else []
    answer["row_reviews"] = [row]
    answer["gaps"] = [
        {"scenario_id": ids[0], "gap_class": "MISSING_ASSERTION",
         "evidence": f"distinct missing behavior {i}"}
        for i in range(gap_count)
    ] if assessment == "GAP" else []
    answer["verdict"] = ("SCENARIO_TEST_COVERAGE_FAIL" if assessment == "GAP"
                         else "SCENARIO_TEST_COVERAGE_PASS")
    obj[0]["turn"]["steps"][0]["message"]["text"] = json.dumps(answer)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path, meta


@pytest.mark.parametrize("gap_count", [1, 2, 3, 6])
def test_single_row_multi_gap_is_preserved_and_never_pass(tmp_path, monkeypatch, gap_count):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, gap_count=gap_count)
    preview = _run(tmp_path, artifacts, write=False)
    assert preview["review_mode"] == "single"
    assert preview["sdk_calls"] == 0
    assert preview["written"]["observations"] == 0
    report = _run(tmp_path, artifacts, write=True)
    assert report["counts"]["GAP"] == 1
    assert report["written"] == {"observations": 1, "supported_receipts": 0}
    assert report["aggregate_verdict"] == "NOT_ASSERTED"
    obs = read_observation(tmp_path, change_id="demo", scenario_id=ids[0])
    assert len(obs["gaps"]) == gap_count
    assert len(set(x["evidence"] for x in obs["gaps"])) == gap_count
    assert not list((tmp_path / "opsx/receipts/demo").rglob("scenario_supported_*.json"))


def test_single_supported_is_independently_proof_checked(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, assessment="SUPPORTED")
    before = _run(tmp_path, artifacts, write=False)
    assert before["eligible_supported_receipts"] == 1
    _assert_recovery_outputs_absent(tmp_path)
    after = _run(tmp_path, artifacts, write=True)
    assert after["written"] == {"observations": 1, "supported_receipts": 1}
    scope = row_recovery.impact_scope(
        tmp_path, row_recovery.source_paths(tmp_path, "demo"), mapping,
        authority={"model": {"id": "composer-2.5", "options": []},
                   "plan_evidence_id": "frozen", "verifier_contract_digest": "policy-v1"},
    )
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[0], mapping=mapping,
        row_digest=scope["row_digests"][ids[0]], plan_evidence_id="frozen"
    ) is not None


@pytest.mark.parametrize("defect", [
    "foreign_gap", "missing_gaps", "duplicate_row", "wrong_verdict",
    "missing_proofs", "wrong_mapping", "stale_plan", "stale_source",
])
def test_single_row_corruption_rejected_before_write(tmp_path, monkeypatch, defect):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    path, meta = _single_artifacts(
        tmp_path, mapping, ids, assessment="SUPPORTED" if defect == "missing_proofs" else "GAP"
    )
    obj = json.loads(path.read_text(encoding="utf-8"))
    answer = json.loads(obj[0]["turn"]["steps"][0]["message"]["text"])
    if defect == "foreign_gap":
        answer["gaps"][0]["scenario_id"] = ids[1]
    elif defect == "missing_gaps":
        answer["gaps"] = []
    elif defect == "duplicate_row":
        answer["row_reviews"] *= 2
    elif defect == "wrong_verdict":
        answer["verdict"] = "SCENARIO_TEST_COVERAGE_PASS"
    elif defect == "missing_proofs":
        answer["row_reviews"][0]["when_proofs"] = []
    elif defect == "wrong_mapping":
        answer["row_reviews"][0]["test_node_id"] = mapping[ids[1]]
    elif defect == "stale_plan":
        answer["plan_evidence_id"] = "not-current"
    elif defect == "stale_source":
        answer["source_digest"] = "x" * 64
    obj[0]["turn"]["steps"][0]["message"]["text"] = json.dumps(answer)
    path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="RECOVERY_"):
        _run(tmp_path, (path, meta), write=True)
    _assert_recovery_outputs_absent(tmp_path)


def test_single_bad_supported_proofs_remain_unverified(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, assessment="SUPPORTED")
    monkeypatch.setattr(row_receipts, "review_proof_error",
                        lambda *args, **kwargs: "PROOF_CLAUSES_MISMATCH")
    result = _run(tmp_path, artifacts, write=True)
    assert result["rejected_supported_proofs"] == {ids[0]: "PROOF_CLAUSES_MISMATCH"}
    assert result["eligible_supported_receipts"] == 0
    assert result["written"] == {"observations": 0, "supported_receipts": 0}
    _assert_recovery_outputs_absent(tmp_path)


def test_single_recovery_is_idempotent_by_scenario_path(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, gap_count=3)
    first = _run(tmp_path, artifacts, write=True)
    second = _run(tmp_path, artifacts, write=True)
    assert first["written"] == second["written"]
    obs_dir = tmp_path / "opsx/receipts/demo/scenario-rows"
    assert len(list(obs_dir.glob("*.json"))) == 1
    assert len(read_observation(tmp_path, change_id="demo", scenario_id=ids[0])["gaps"]) == 3

def test_native_supported_receipt_write_failure_is_not_silent_success(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, assessment="SUPPORTED")
    monkeypatch.setattr(row_recovery, "save_supported_row", lambda *args, **kwargs: False)
    with pytest.raises(ValueError, match="RECOVERY_PASS_RECEIPT_NOT_PERSISTED"):
        _run(tmp_path, artifacts, write=True)
    # The diagnostic observation may survive an interrupted writer. Only the
    # independently persisted positive receipt has authority.
    assert read_observation(tmp_path, change_id="demo", scenario_id=ids[0]) is not None
    assert not list((tmp_path / "opsx/receipts/demo").rglob(
        "scenario_supported_*.json"
    ))

def _stream_pair(root, artifacts, *, corrupt="", bad_path=False):
    """Mirror the real SDK transport: stream and canonical metadata are separate."""
    src, meta_path = artifacts
    answer = json.loads(json.loads(src.read_text(encoding="utf-8"))[0]["turn"]["steps"][0]["message"]["text"])
    sid = answer["row_reviews"][0]["scenario_id"]
    key = row_receipts.row_key(sid)
    if bad_path:
        key = "wrong-" + key
    stream = (
        root
        / "opsx/logs/demo/legacy/scenario-row-streams"
        / "historical-execution"
        / (key + ".conversation.json")
    )
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_bytes(src.read_bytes())
    canonical = (root / "opsx/opsx_runtime/run_dialogs/demo/historical-execution"
                 / "scenario_coverage_verify/revisions/sdk-old/meta.json")
    canonical.parent.mkdir(parents=True, exist_ok=True)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["legacy_conversation_json"] = "/agent/repos/stroy-system/" + stream.relative_to(root).as_posix()
    if corrupt == "wrong_execution":
        meta["run_uuid"] = "another-execution"
    elif corrupt == "wrong_legacy":
        meta["legacy_conversation_json"] = "/agent/repos/stroy-system/foreign.json"
    elif corrupt == "wrong_sdk_run":
        meta["sdk_run_id"] = "not-the-directory"
    canonical.write_text(json.dumps(meta), encoding="utf-8")
    return stream, canonical


def test_stream_conversation_and_revision_metadata_can_recover_multi_gap(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, gap_count=3)
    stream = _stream_pair(tmp_path, artifacts)
    preview = _run(tmp_path, stream, write=False)
    assert preview["review_mode"] == "single"
    assert preview["sdk_calls"] == 0
    assert preview["written"] == {"observations": 0, "supported_receipts": 0}
    saved = _run(tmp_path, stream, write=True)
    assert saved["written"] == {"observations": 1, "supported_receipts": 0}
    obs = read_observation(tmp_path, change_id="demo", scenario_id=ids[0])
    assert len(obs["gaps"]) == 3


@pytest.mark.parametrize("defect", ["wrong_execution", "wrong_legacy", "wrong_sdk_run", "wrong_filename"])
def test_stream_cross_directory_identity_is_fail_closed(tmp_path, monkeypatch, defect):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _single_artifacts(tmp_path, mapping, ids, assessment="SUPPORTED")
    stream = _stream_pair(
        tmp_path, artifacts,
        corrupt=defect if defect != "wrong_filename" else "",
        bad_path=defect == "wrong_filename",
    )
    with pytest.raises(ValueError, match="RECOVERY_"):
        _run(tmp_path, stream, write=True)
    assert not list((tmp_path / "opsx/receipts/demo").rglob(
        "scenario_supported_*.json"
    ))


@pytest.mark.parametrize("assessment", ["GAP", "INSPECTION_BLOCKED"])
def test_recovered_single_negative_is_reusable_without_new_sdk(tmp_path, monkeypatch, assessment):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids)
    conversation = json.loads(artifacts[0].read_text(encoding="utf-8"))
    answer = json.loads(conversation[0]["turn"]["steps"][0]["message"]["text"])
    answer["row_reviews"] = [answer["row_reviews"][0]]
    answer["row_reviews"][0]["assessment"] = assessment
    answer["row_reviews"][0]["when_proofs"] = []
    answer["row_reviews"][0]["outcome_proofs"] = []
    answer["verdict"] = (
        "SCENARIO_TEST_COVERAGE_FAIL" if assessment == "GAP"
        else "SCENARIO_TEST_COVERAGE_UNKNOWN"
    )
    answer["gaps"] = (
        [{"scenario_id": ids[0], "gap_class": "NO_ASSERT", "evidence": "missing assertion"}]
        if assessment == "GAP" else []
    )
    answer["block_reason"] = "cannot inspect source" if assessment != "GAP" else ""
    conversation[0]["turn"]["steps"][0]["message"]["text"] = json.dumps(answer)
    artifacts[0].write_text(json.dumps(conversation), encoding="utf-8")

    report = _run(tmp_path, artifacts, write=True)
    assert report["written"] == {"observations": 1, "supported_receipts": 0}
    from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
    from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
    scope = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping,
        authority={
            "model": {"id": "composer-2.5", "options": []},
            "plan_evidence_id": "frozen", "verifier_contract_digest": "policy-v1",
        },
    )
    reused = row_receipts.read_reusable_terminal_row(
        tmp_path, change_id="demo", scenario_id=ids[0],
        expected_node=mapping[ids[0]],
        row_digest=scope["row_digests"][ids[0]],
        shared_digest=scope["shared_digest"],
        source_digest=input_snapshot(tmp_path, "demo")["source_digest"],
        plan_evidence_id="frozen",
    )
    assert reused is not None
    row, gaps, block_reason, provenance = reused
    assert row.assessment == assessment
    assert provenance["origin_execution_id"] == "historical-execution"
    assert len(gaps) == int(assessment == "GAP")
    assert bool(block_reason) == (assessment == "INSPECTION_BLOCKED")
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[0],
        mapping=mapping, row_digest=scope["row_digests"][ids[0]],
        plan_evidence_id="frozen",
    ) is None


def test_incomplete_legacy_negative_stays_diagnostic_only(tmp_path, monkeypatch):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids)
    _run(tmp_path, artifacts, write=True)
    from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
    from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
    scope = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping,
        authority={
            "model": {"id": "composer-2.5", "options": []},
            "plan_evidence_id": "frozen", "verifier_contract_digest": "policy-v1",
        },
    )
    for sid in ids[1:]:
        assert row_receipts.read_reusable_terminal_row(
            tmp_path, change_id="demo", scenario_id=sid,
            expected_node=mapping[sid],
            row_digest=scope["row_digests"][sid],
            shared_digest=scope["shared_digest"],
            source_digest=input_snapshot(tmp_path, "demo")["source_digest"],
            plan_evidence_id="frozen",
        ) is None


def test_pinned_v2_source_can_be_rebound_to_v3_receipt_without_model(
    tmp_path, monkeypatch,
):
    """A source-hash contract upgrade is not evidence of product changes."""
    from agents.verify.scenario_coverage_verify.evidence_contract import legacy_source_digest

    ids, mapping = _workspace(tmp_path, monkeypatch)
    conversation, meta = _single_artifacts(tmp_path, mapping, ids, assessment="GAP")
    payload = json.loads(conversation.read_text(encoding="utf-8"))
    answer = json.loads(payload[0]["turn"]["steps"][0]["message"]["text"])
    old_source = legacy_source_digest(tmp_path, "demo")
    new_source = input_snapshot(tmp_path, "demo")["source_digest"]
    assert old_source != new_source
    answer["source_digest"] = old_source
    payload[0]["turn"]["steps"][0]["message"]["text"] = json.dumps(answer)
    conversation.write_text(json.dumps(payload), encoding="utf-8")

    # _workspace replaces the pinned Git attestor with a focused stub. The
    # independent attestor has its own tests; here exercise the v2→v3 rebind.
    report = _run(tmp_path, (conversation, meta), write=True)
    assert report["written"] == {"observations": 1, "supported_receipts": 0}
    assert report["sdk_calls"] == 0
    assert report["source_digest"] == new_source
    observation = read_observation(tmp_path, change_id="demo", scenario_id=ids[0])
    assert observation is not None
    assert observation["source_digest"] == new_source
    assert observation["recovery_review_mode"] == "SINGLE_ROW_VALIDATED"


def test_recovery_batch_negative_is_never_published_as_reusable_evidence(
    tmp_path, monkeypatch,
):
    ids, mapping = _workspace(tmp_path, monkeypatch)
    artifacts = _artifacts(tmp_path, mapping, ids)
    _run(tmp_path, artifacts, write=True)
    for sid in ids[1:]:
        obs = read_observation(tmp_path, change_id="demo", scenario_id=sid)
        assert obs is not None
        assert obs["recovery_review_mode"] == "BATCH_DIAGNOSTIC_ONLY"
        assert not obs["shared_digest"]


def test_git_v3_source_attestation_excludes_foreign_change_files_and_tests(
    tmp_path, monkeypatch,
):
    _workspace(tmp_path, monkeypatch)
    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "add", "."],
        ["git", "commit", "-m", "frozen v3 scenario source"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    anchor = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    original = input_snapshot(tmp_path, "demo")["source_digest"]
    at_commit, selected = row_recovery.source_digest_v3_at_git_ref(
        tmp_path, anchor, "demo",
    )
    assert at_commit == original
    assert "tests/test_a.py" in selected

    foreign = tmp_path / "openspec/changes/foreign/specs"
    foreign.mkdir(parents=True)
    (foreign / "spec.md").write_text("changed foreign spec\n", encoding="utf-8")
    (tmp_path / "tests/test_foreign.py").write_text(
        "def test_foreign():\n    assert False\n", encoding="utf-8",
    )
    assert input_snapshot(tmp_path, "demo")["source_digest"] == original
    assert row_recovery.source_digest_v3_at_git_ref(
        tmp_path, anchor, "demo",
    )[0] == original

    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert False\n", encoding="utf-8",
    )
    assert input_snapshot(tmp_path, "demo")["source_digest"] != original
    assert row_recovery.source_digest_v3_at_git_ref(
        tmp_path, anchor, "demo",
    )[0] == original


def test_pinned_v3_attestation_matches_windows_git_crlf_checkout(
    tmp_path, monkeypatch,
):
    """Git-normalized blobs (LF) must reconstruct worktree snapshots (CRLF)."""
    from agents.verify.scenario_coverage_verify.evidence_contract import source_paths

    _workspace(tmp_path, monkeypatch)
    # Explicit CRLF bytes make the scenario reproducible on Linux and Windows.
    for path in source_paths(tmp_path, "demo"):
        source = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(source.replace(b"\n", b"\r\n"))
    commands = (
        ["git", "init"],
        ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "config", "core.autocrlf", "true"],
        ["git", "add", "."],
        ["git", "commit", "-m", "git normalizes checkout CRLF"],
    )
    for command in commands:
        subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    raw_blob = subprocess.run(
        ["git", "show", f"{sha}:tests/test_a.py"], cwd=tmp_path,
        capture_output=True, check=True,
    ).stdout
    assert b"\r\n" not in raw_blob
    assert b"\r\n" in (tmp_path / "tests/test_a.py").read_bytes()

    expected = input_snapshot(tmp_path, "demo")["source_digest"]
    actual, files = row_recovery.source_digest_v3_at_git_ref(
        tmp_path, sha, "demo",
    )
    assert actual == expected
    assert "tests/test_a.py" in files


def test_pinned_v3_attestation_refuses_external_checkout_filters(
    tmp_path, monkeypatch,
):
    """Read-only recovery cannot execute repository-defined smudge commands."""
    _workspace(tmp_path, monkeypatch)
    (tmp_path / ".gitattributes").write_text(
        "tests/test_a.py filter=untrusted\n", encoding="utf-8",
    )
    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "add", "."],
        ["git", "commit", "-m", "checkout filter declaration"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    with pytest.raises(ValueError, match="RECOVERY_V3_EXTERNAL_FILTER_UNSAFE"):
        row_recovery.source_digest_v3_at_git_ref(tmp_path, sha, "demo")
