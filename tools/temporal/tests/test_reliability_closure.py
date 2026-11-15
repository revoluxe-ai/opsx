"""Негативная приёмка identity, write-scope, recovery и model authority.

Ложные зелёные исходы (mtime, грязное дерево, чужой receipt, Fast/GPT/inherit)
не авторизуют materialize и не подменяют модель.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from model_authority import authorize_cli_model
from models import classify_completed_repair
from parsers import parse_opsx_bug_fix_result
from recovery_contract import (
    FAIL_RECURRING,
    PAUSE_EXPANDING,
    PAUSE_NO_PROGRESS,
    PAUSE_REGRESSION,
    PAUSE_UNRECONCILED,
    contract_for_uncertain,
    findings_contract,
    progress_contract,
    recurring_failure,
    refuse_unreconciled_retry,
)
from repair_evidence_identity import (
    PHASE_POST,
    PHASE_PRE,
    SCHEMA_VERSION,
    RepairEvidenceContext,
    canonical_content_sha256,
    evaluate_post_materialize,
    evaluate_pre_materialize,
    receipt_path,
)
from repair_progress_evidence import apply_durable_repair_progress
from write_ownership import TERMINAL_WRITE_OWNERSHIP, capture_worktree, evaluate_write_ownership


def _context(**overrides: object) -> RepairEvidenceContext:
    payload = {
        "orchestration_execution_id": "exec-1",
        "workflow_id": "wf-1",
        "repair_number": 1,
        "review_fingerprint": "fp-1",
        "base_sha": "base-sha",
        "head_sha": "head-sha",
        "workflow_run_id": "run-1",
    }
    payload.update(overrides)
    return RepairEvidenceContext(**payload)  # type: ignore[arg-type]


def _write_receipt(repo: Path, context: RepairEvidenceContext, **overrides: object) -> dict:
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "evidence_phase": PHASE_PRE,
        "orchestration_execution_id": context.orchestration_execution_id,
        "workflow_id": context.workflow_id,
        "workflow_run_id": context.workflow_run_id,
        "repair_number": context.repair_number,
        "review_fingerprint": context.review_fingerprint,
        "revision_identity": {"base_sha": context.base_sha, "head_sha": context.head_sha},
        "terminal_status": "closed_fixed",
        "written_at": "2026-10-24T00:00:00Z",
    }
    payload.update(overrides)
    payload["content_sha256"] = canonical_content_sha256(payload)
    path = repo / receipt_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)


def test_matching_self_hash_is_not_sole_proof(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    context = _context()
    _write_receipt(tmp_path, context, producer="opsx")
    verdict = evaluate_pre_materialize(
        tmp_path,
        context,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_GAP"
    assert "not sole proof" in verdict.reason
    parsed = parse_opsx_bug_fix_result(
        "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"
    )
    applied = apply_durable_repair_progress(
        parsed,
        tmp_path,
        before_fingerprint="",
        since_epoch=0,
        context=context,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert applied.repaired_this_batch is False
    assert applied.terminal_status == "BLOCKED_EVIDENCE_GAP"


def test_product_dirty_tree_alone_does_not_authorize(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "app").mkdir()
    target = tmp_path / "app" / "app.js"
    target.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/app.js"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    baseline = capture_worktree(tmp_path)
    target.write_text("v2\n", encoding="utf-8")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    parsed = parse_opsx_bug_fix_result(
        "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"
    )
    unbound = apply_durable_repair_progress(
        parsed,
        tmp_path,
        before_fingerprint="before",
        since_epoch=0,
        context=None,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert unbound.repaired_this_batch is False
    assert unbound.terminal_status == "BLOCKED_EVIDENCE_GAP"
    bound = apply_durable_repair_progress(
        parse_opsx_bug_fix_result(
            "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"
        ),
        tmp_path,
        before_fingerprint="before",
        since_epoch=0,
        context=_context(head_sha=head),
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert bound.repaired_this_batch is True
    assert target.read_text(encoding="utf-8") == "v2\n"
    wrong_revision = apply_durable_repair_progress(
        parse_opsx_bug_fix_result(
            "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"
        ),
        tmp_path,
        before_fingerprint="before",
        since_epoch=0,
        context=_context(head_sha="other-revision"),
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert wrong_revision.repaired_this_batch is False
    assert wrong_revision.terminal_status == "BLOCKED_EVIDENCE_IDENTITY"


def test_forged_ledger_json_is_not_repair_evidence(tmp_path: Path) -> None:
    _git_init(tmp_path)
    context = _context()
    _write_receipt(tmp_path, context, producer="cursor_executor", execution_id="exec-forged")
    forged_ledger = tmp_path / "opsx" / "cursor-executor-ledger" / "exec-1" / "record.json"
    forged_ledger.parent.mkdir(parents=True)
    forged_ledger.write_text(
        json.dumps({"execution_id": "exec-1", "producer": "cursor_executor"}),
        encoding="utf-8",
    )
    verdict = evaluate_pre_materialize(
        tmp_path,
        context,
        baseline=capture_worktree(tmp_path),
        authorized_prefixes=("app",),
    )
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_GAP"


def test_forged_matching_receipt_is_not_authorized(tmp_path: Path) -> None:
    _git_init(tmp_path)
    context = _context()
    payload = _write_receipt(tmp_path, context, producer="opsx")
    assert payload["content_sha256"] == canonical_content_sha256(payload)
    verdict = evaluate_pre_materialize(
        tmp_path,
        context,
        baseline=capture_worktree(tmp_path),
        authorized_prefixes=("app",),
    )
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_GAP"
    assert "not sole proof" in verdict.reason


def test_altered_identity_with_recalculated_hash_is_rejected(tmp_path: Path) -> None:
    context = _context()
    payload = _write_receipt(tmp_path, context, review_fingerprint="forged-fp")
    assert payload["content_sha256"] == canonical_content_sha256(payload)
    verdict = evaluate_pre_materialize(tmp_path, context)
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_IDENTITY"


def test_receipt_without_observed_change_is_not_authorized(tmp_path: Path) -> None:
    _git_init(tmp_path)
    context = _context()
    payload = _write_receipt(tmp_path, context)
    verdict = evaluate_pre_materialize(
        tmp_path,
        context,
        baseline=capture_worktree(tmp_path),
        authorized_prefixes=("app",),
    )
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_GAP"
    post = evaluate_post_materialize(
        tmp_path,
        context,
        head_before="head-sha",
        head_after="after-sha",
        commit_status="COMPLETE",
        tree_clean=True,
    )
    assert post.authorized is False
    assert payload["content_sha256"]


def test_foreign_stale_and_replayed_receipts_do_not_authorize(tmp_path: Path) -> None:
    context = _context()
    _write_receipt(tmp_path, context, orchestration_execution_id="other-exec")
    foreign = evaluate_pre_materialize(tmp_path, context)
    assert foreign.authorized is False
    assert foreign.terminal == "BLOCKED_EVIDENCE_IDENTITY"

    stale = _context(head_sha="other-head")
    _write_receipt(tmp_path, stale)
    mismatch = evaluate_pre_materialize(tmp_path, context)
    assert mismatch.terminal == "BLOCKED_EVIDENCE_IDENTITY"

    replay = _context(repair_number=2)
    _write_receipt(tmp_path, replay, repair_number=1)
    # path is repair-2, body says repair 1
    replayed = evaluate_pre_materialize(tmp_path, replay)
    assert replayed.terminal == "BLOCKED_EVIDENCE_IDENTITY"


def test_fresh_mtime_foreign_file_is_ignored(tmp_path: Path) -> None:
    materialize = tmp_path / "opsx/runs/bug-cases/materialize"
    materialize.mkdir(parents=True)
    stray = materialize / "closed-foreign.json"
    stray.write_text(json.dumps({"terminal_status": "closed_fixed"}), encoding="utf-8")
    os.utime(stray, None)
    verdict = evaluate_pre_materialize(tmp_path, _context())
    assert verdict.authorized is False
    assert verdict.terminal == "BLOCKED_EVIDENCE_GAP"


def test_corrupt_hash_and_post_phase_fail_closed(tmp_path: Path) -> None:
    context = _context()
    payload = _write_receipt(tmp_path, context)
    path = tmp_path / receipt_path(context)
    payload["content_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    corrupt = evaluate_pre_materialize(tmp_path, context)
    assert corrupt.terminal == "BLOCKED_EVIDENCE_INTEGRITY"

    post_context = _context(repair_number=3)
    _write_receipt(tmp_path, post_context, evidence_phase=PHASE_POST, head_after="after")
    ordered = evaluate_pre_materialize(tmp_path, post_context)
    assert ordered.authorized is False
    assert ordered.terminal == "BLOCKED_EVIDENCE_ORDERING"
    post = evaluate_post_materialize(
        tmp_path,
        post_context,
        head_before="head-sha",
        head_after="after",
        commit_status="COMPLETE",
        tree_clean=True,
    )
    assert post.authorized is False
    assert post.terminal == "BLOCKED_EVIDENCE_IDENTITY"


def test_pre_receipt_is_not_post_completion(tmp_path: Path) -> None:
    context = _context(repair_number=4)
    _write_receipt(tmp_path, context)
    post = evaluate_post_materialize(
        tmp_path,
        context,
        head_before="head-sha",
        head_after="head-sha",
        commit_status="COMPLETE",
        tree_clean=True,
    )
    assert post.authorized is False
    assert post.terminal == "BLOCKED_EVIDENCE_IDENTITY"


def test_preexisting_dirty_tree_is_not_reset(tmp_path: Path) -> None:
    _git_init(tmp_path)
    owned = tmp_path / "owner.txt"
    owned.write_text("owner\n", encoding="utf-8")
    baseline = capture_worktree(tmp_path)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert verdict.blocked is True
    assert verdict.terminal == TERMINAL_WRITE_OWNERSHIP
    assert owned.read_text(encoding="utf-8") == "owner\n"
    source = Path(__file__).resolve().parents[1].joinpath("write_ownership.py").read_text(encoding="utf-8")
    assert '["git", "reset"]' not in source
    assert '["git", "checkout"]' not in source
    assert '["git", "clean"]' not in source


def test_tmp_owner_file_is_not_scratch(tmp_path: Path) -> None:
    _git_init(tmp_path)
    owned = tmp_path / ".tmp" / "owner.txt"
    owned.parent.mkdir()
    owned.write_text("owner\n", encoding="utf-8")
    baseline = capture_worktree(tmp_path)
    preexisting = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert preexisting.blocked is True
    assert preexisting.terminal == TERMINAL_WRITE_OWNERSHIP
    assert owned.read_text(encoding="utf-8") == "owner\n"

    clean = capture_worktree(tmp_path)
    # Снимок снят до появления файла: новый путь всё равно вне scope.
    owned.unlink()
    empty = capture_worktree(tmp_path)
    owned.write_text("owner\n", encoding="utf-8")
    created = evaluate_write_ownership(
        tmp_path,
        baseline=empty,
        authorized_prefixes=("app",),
    )
    assert created.blocked is True
    assert ".tmp/owner.txt" in created.reason
    assert clean.digests  # файл был виден git до удаления


def test_preflight_scratch_is_not_owner_scope(tmp_path: Path) -> None:
    _git_init(tmp_path)
    artifact = tmp_path / ".tmp" / "temporal" / "git-preflight-latest.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("preflight\n", encoding="utf-8")
    baseline = capture_worktree(tmp_path)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert verdict.blocked is False
    foreign = tmp_path / ".tmp" / "temporal" / "stolen.txt"
    foreign.write_text("x\n", encoding="utf-8")
    stolen = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert stolen.blocked is True
    assert ".tmp/temporal/stolen.txt" in stolen.reason


def test_preexisting_receipt_is_not_absorbed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    receipt = tmp_path / "opsx" / "runs" / "bug-cases" / "materialize" / "foreign.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}\n", encoding="utf-8")
    baseline = capture_worktree(tmp_path)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert verdict.blocked is True
    assert "foreign.json" in verdict.reason


def test_path_outside_scope_is_foreign(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("app",),
    )
    assert verdict.blocked is True
    assert "notes.txt" in verdict.reason


def test_recovery_contracts() -> None:
    blind = contract_for_uncertain(repo_available=True, inspected=False)
    assert blind.status == PAUSE_UNRECONCILED
    assert refuse_unreconciled_retry(blind) is not None
    uncompared = contract_for_uncertain(repo_available=True, inspected=True)
    assert uncompared.status == PAUSE_UNRECONCILED
    drifted = contract_for_uncertain(
        repo_available=True, inspected=True, side_effects_match_baseline=False
    )
    assert drifted.status == PAUSE_UNRECONCILED
    reconciled = contract_for_uncertain(
        repo_available=True, inspected=True, side_effects_match_baseline=True
    )
    assert reconciled.reconciled is True
    assert refuse_unreconciled_retry(reconciled) is None
    assert recurring_failure(("BLOCKED_EVIDENCE_GAP",), "BLOCKED_EVIDENCE_GAP").status == FAIL_RECURRING
    assert progress_contract(previous_token="same", current_token="same").status == PAUSE_NO_PROGRESS
    assert findings_contract(
        previous_unresolved=1,
        current_unresolved=2,
        previous_fingerprint="a",
        current_fingerprint="a",
    ).status == PAUSE_EXPANDING
    assert findings_contract(
        previous_unresolved=2,
        current_unresolved=2,
        previous_fingerprint="a",
        current_fingerprint="b",
    ).status == PAUSE_REGRESSION


def test_pause_and_blocked_tokens_skip_materialize() -> None:
    for token in (
        "PAUSE_NO_DEMONSTRABLE_PROGRESS",
        "PAUSE_RECOVERY_UNRECONCILED",
        "FAIL_CLOSED_RECURRING_CAUSE",
        "BLOCKED_EVIDENCE_INTEGRITY",
        "BLOCKED_EVIDENCE_ORDERING",
    ):
        stop = classify_completed_repair(
            terminal_status=token,
            repaired_this_batch=True,
            bug_batch_clear=True,
        )
        assert stop is not None
        assert stop[0] == token


def test_cli_omitted_inherit_and_fast_are_not_composer_standard() -> None:
    with pytest.raises(ValueError, match="MODEL_AUTHORITY_BLOCKED"):
        authorize_cli_model("")
    with pytest.raises(ValueError, match="MODEL_AUTHORITY_BLOCKED"):
        authorize_cli_model("inherit")
    with pytest.raises(ValueError, match="MODEL_AUTHORITY_BLOCKED"):
        authorize_cli_model("composer-2.5-fast")
    with pytest.raises(ValueError, match="MODEL_AUTHORITY_BLOCKED"):
        authorize_cli_model("gpt-5.6-sol")
    assert authorize_cli_model("composer-2.5", speed="standard") == "composer-2.5"


def test_observed_transition_binds_and_wrong_parent_does_not(tmp_path: Path) -> None:
    from commit_materialization import materialize_if_needed
    from cursor_adapter import PromptInvocation
    from models import RepairRunResult

    _git_init(tmp_path)
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("opsx/\n.tmp/\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    target = tmp_path / "app" / "app.js"
    target.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/app.js"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    target.write_text("v2\n", encoding="utf-8")
    context = _context(head_sha=before)

    class _Committer:
        def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
            del step
            subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "fix"], cwd=tmp_path, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            return PromptInvocation(
                prompt=prompt,
                raw_output="STATUS: COMPLETE\n",
                exit_code=0,
                session_id="s",
            )

    result = materialize_if_needed(
        repo_path=tmp_path,
        adapter=_Committer(),
        repair=RepairRunResult(
            completed=True,
            bug_batch_clear=True,
            unresolved=[],
            running_worker=None,
            raw_output="STATUS: BUG_BATCH_CLEAR\nREPAIRED_THIS_BATCH: YES\n",
            repaired_this_batch=True,
            terminal_status="BUG_BATCH_CLEAR",
        ),
        evidence_context=context,
    )
    assert result.invoked is True
    assert result.ok is True
    assert result.post_bound is True
    assert result.status == "COMPLETE"
    assert result.candidate_transitioned is True
    assert result.head_before == before
    assert result.head_after not in {None, before}
    parent = subprocess.run(
        ["git", "rev-parse", f"{result.head_after}^"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert parent == before
    assert target.read_text(encoding="utf-8") == "v2\n"
    outside = evaluate_post_materialize(
        tmp_path,
        context,
        head_before=before,
        head_after=result.head_after or "",
        commit_status="COMPLETE",
        tree_clean=True,
        authorized_prefixes=("tests",),
    )
    assert outside.authorized is False
    assert "outside authorized write scope" in outside.reason
    wrong = evaluate_post_materialize(
        tmp_path,
        _context(head_sha="not-the-parent"),
        head_before=before,
        head_after=result.head_after or "",
        commit_status="COMPLETE",
        tree_clean=True,
    )
    assert wrong.authorized is False
    assert wrong.terminal == "BLOCKED_EVIDENCE_IDENTITY"
    _write_receipt(
        tmp_path,
        context,
        evidence_phase=PHASE_POST,
        head_after="forged-head",
    )
    contradicted = evaluate_post_materialize(
        tmp_path,
        context,
        head_before=before,
        head_after=result.head_after or "",
        commit_status="COMPLETE",
        tree_clean=True,
    )
    assert contradicted.authorized is False
    assert contradicted.terminal == "BLOCKED_EVIDENCE_IDENTITY"


def test_workflow_passes_identity_before_materialize() -> None:
    source = Path(__file__).resolve().parents[1].joinpath("workflow.py").read_text(encoding="utf-8")
    identity = source.index("orchestration_execution_id")
    materialize = source.index("execute_commit_materialize")
    assert identity < materialize
    assert "review_fingerprint" in source
    bound = source.index("post_bound")
    head_update = source.index('config.head = last_materialize["head_after"]')
    assert bound < head_update
    assert "_reliability_payload_fields" in source
    assert "**reliability" in source[materialize:bound]
