"""Acceptance tests for atomic PBH admission manifest publication (T1–T6)."""

from __future__ import annotations

import json
import threading

import pytest

from control_plane_code_identity import compute_control_plane_code_identity
from pbh_admission_binding import (
    AdmissionManifestConflictError,
    admission_manifest_path,
    is_admission_manifest_complete,
    load_admission_manifest,
    publish_admission_manifest_atomic,
    validate_admission_binding,
)


def _identity() -> str:
    return compute_control_plane_code_identity()


def test_t1_duplicate_candidate_publish_does_not_mutate_active_run_manifest(tmp_path):
    """T1: run A evidence unchanged when a second run binding is published."""
    repo = tmp_path
    wf = "pbh-t1-singleton"
    head_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    head_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ident = _identity()
    publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id="run-a",
        expected_candidate_sha=head_a,
        control_plane_code_identity=ident,
    )
    publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id="run-b",
        expected_candidate_sha=head_b,
        control_plane_code_identity=ident,
    )
    manifest_a = load_admission_manifest(repo, wf, "run-a")
    assert manifest_a is not None
    assert manifest_a["expected_candidate_sha"] == head_a
    err, _ = validate_admission_binding(
        manifest_a,
        workflow_id=wf,
        workflow_run_id="run-a",
        observed_candidate_sha=head_a,
        expected_control_plane_code_identity=ident,
    )
    assert err is None


def test_t2_concurrent_publish_same_run_id_is_idempotent(tmp_path):
    """T2: concurrent publishers cannot overwrite a conflicting authority."""
    repo = tmp_path
    wf = "pbh-t2-concurrent"
    run_id = "run-concurrent-001"
    head = "cccccccccccccccccccccccccccccccccccccccc"
    ident = _identity()
    errors: list[BaseException] = []

    def _publish() -> None:
        try:
            publish_admission_manifest_atomic(
                repo,
                workflow_id=wf,
                workflow_run_id=run_id,
                expected_candidate_sha=head,
                control_plane_code_identity=ident,
            )
        except BaseException as exc:  # noqa: BLE001 — test harness
            errors.append(exc)

    threads = [threading.Thread(target=_publish) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not any(isinstance(e, AdmissionManifestConflictError) for e in errors)
    manifest = load_admission_manifest(repo, wf, run_id)
    assert manifest is not None
    assert manifest["expected_candidate_sha"] == head


def test_t3_interrupted_publication_yields_no_complete_manifest(tmp_path):
    """T3: partial tmp without replace is not loadable authority."""
    repo = tmp_path
    wf = "pbh-t3-partial"
    run_id = "run-partial"
    path = admission_manifest_path(repo, wf, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {
                "schema_version": "pbh-admission-binding-v1",
                "workflow_id": wf,
                "workflow_run_id": "",
                "expected_candidate_sha": "deadbeef",
            }
        ),
        encoding="utf-8",
    )
    assert load_admission_manifest(repo, wf, run_id) is None
    assert not is_admission_manifest_complete(json.loads(tmp.read_text(encoding="utf-8")))


def test_t4_same_run_identical_input_is_idempotent(tmp_path):
    """T4: republication with identical binding is a no-op."""
    repo = tmp_path
    wf = "pbh-t4-idempotent"
    run_id = "run-idem"
    head = "dddddddddddddddddddddddddddddddddddddddd"
    ident = _identity()
    first = publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id=run_id,
        expected_candidate_sha=head,
        control_plane_code_identity=ident,
    )
    second = publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id=run_id,
        expected_candidate_sha=head,
        control_plane_code_identity=ident,
    )
    assert first == second
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_t5_same_run_different_candidate_rejects(tmp_path):
    """T5: conflicting republication for one run_id fails closed."""
    repo = tmp_path
    wf = "pbh-t5-conflict"
    run_id = "run-conflict"
    ident = _identity()
    publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id=run_id,
        expected_candidate_sha="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        control_plane_code_identity=ident,
    )
    with pytest.raises(AdmissionManifestConflictError):
        publish_admission_manifest_atomic(
            repo,
            workflow_id=wf,
            workflow_run_id=run_id,
            expected_candidate_sha="ffffffffffffffffffffffffffffffffffffffff",
            control_plane_code_identity=ident,
        )


def test_t6_old_run_manifest_cannot_satisfy_new_run(tmp_path):
    """T6: new execution run_id does not inherit prior run's manifest."""
    repo = tmp_path
    wf = "pbh-t6-run-isolation"
    ident = _identity()
    head = "1111111111111111111111111111111111111111"
    publish_admission_manifest_atomic(
        repo,
        workflow_id=wf,
        workflow_run_id="run-old",
        expected_candidate_sha=head,
        control_plane_code_identity=ident,
    )
    assert load_admission_manifest(repo, wf, "run-new") is None
    err, message = validate_admission_binding(
        None,
        workflow_id=wf,
        workflow_run_id="run-new",
        observed_candidate_sha=head,
        expected_control_plane_code_identity=ident,
    )
    assert err == "WORKER_CODE_IDENTITY_UNVERIFIED"
    assert "run-new" in message
