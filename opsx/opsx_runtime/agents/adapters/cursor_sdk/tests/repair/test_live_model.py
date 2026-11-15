"""Opt-in live model acceptance on the *current* Verify GraphState.

No historical conversation, mock SDK output, synthetic gap or replayed
source digest is allowed. Running this test MAY spend tokens and mutate
tests/** within an externally write-isolated disposable repository.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _live_inputs(request: pytest.FixtureRequest) -> tuple[Path, Path, Path, Path]:
    """Resolve the repo from pytest and report missing live evidence together."""
    repo = Path(
        os.getenv("OPSX_SCENARIO_REPAIR_LIVE_REPOSITORY")
        or str(request.config.rootpath)
    ).resolve()
    if not (repo / "opsx").is_dir():
        pytest.fail(f"LIVE_REPOSITORY_INVALID: {repo}", pytrace=False)

    required = (
        "OPSX_SCENARIO_REPAIR_LIVE_STATE_JSON",
        "OPSX_SCENARIO_REPAIR_ISOLATION_PROOF_JSON",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        pytest.fail(
            "LIVE_EXECUTION_EVIDENCE_MISSING: " + ", ".join(missing)
            + ". pytest -m live_agent selects the test; it cannot invent a "
            "fresh Verify GraphState or an OS/SDK isolation receipt. "
            "No model call was made.",
            pytrace=False,
        )
    state_file = Path(os.environ[required[0]]).resolve()
    proof = Path(os.environ[required[1]]).resolve()
    custom_result_dir = os.getenv("OPSX_SCENARIO_REPAIR_LIVE_RESULTS", "").strip()
    if custom_result_dir:
        result_dir = Path(custom_result_dir).resolve()
    else:
        repo_id = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:12]
        result_dir = Path(tempfile.gettempdir()).resolve() / "opsx-scenario-repair-live" / repo_id
    return repo, state_file, proof, result_dir


def _write_receipt(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


@pytest.mark.live_agent
@pytest.mark.asyncio
async def test_live_model_repairs_current_scenario_fail_and_reaudits(request):
    """Only -m live_agent permits real SDK calls. No additional CLI flag."""
    if str(request.config.option.markexpr or "").strip() != "live_agent":
        pytest.skip("LIVE_AGENT_DISABLED: use explicit pytest -m live_agent")
    repo, state_file, proof, result_dir = _live_inputs(request)
    # Do not import adapter runtime or mutating agents while collecting tests.
    # Mandatory execution inputs are validated before loading those modules.
    try:
        from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
        from agents.repair.scenario_test_repair.live_gate import (
            LiveGateBlocked, prepare_live_state,
        )
        from agents.repair.scenario_test_repair.live_checks import run_focused_checks
        from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot
        from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
        from workflows.repair.repair_adapter_port import RepairAdapter
        from agents.adapters.cursor_sdk.cursor_sdk_adapter import CursorSDKAdapter
    except ImportError as exc:
        pytest.fail(
            f"LIVE_SDK_DEPENDENCY_MISSING: {type(exc).__name__}: {exc}. "
            "Use an environment with OPSX dev dependencies.",
            pytrace=False,
        )
    if result_dir.is_relative_to(repo):
        pytest.fail("LIVE_RESULT_DIR_INVALID: must be OUTSIDE the repository", pytrace=False)
    if state_file.is_relative_to(repo) or proof.is_relative_to(repo):
        pytest.fail("LIVE_INPUT_FILES_MUST_BE_OUTSIDE_TARGET", pytrace=False)
    try:
        state = prepare_live_state(
            state_file, repository_path=repo, isolation_proof_path=proof,
        )
    except LiveGateBlocked as exc:
        pytest.fail(str(exc), pytrace=False)
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        pytest.fail(f"LIVE_RESULT_DIR_UNAVAILABLE: {type(exc).__name__}: {exc}", pytrace=False)

    execution_id = str(state["run_uuid"])
    if not execution_id.replace("_", "").replace("-", "").isalnum() or len(execution_id) > 96:
        pytest.fail("LIVE_EXECUTION_ID_INVALID: unsafe receipt filename", pytrace=False)
    # One attempt only per execution; never auto-resend a mutating prompt after
    # an interrupted SDK call whose writes may remain active or unknown.
    attempt = result_dir / f"scenario-test-repair-{execution_id}.attempt"
    try:
        with attempt.open("x", encoding="utf-8") as stream:
            stream.write(datetime.now(timezone.utc).isoformat() + "\n")
    except FileExistsError:
        pytest.fail("LIVE_RETRY_BLOCKED: reconcile prior SDK run before any retry", pytrace=False)

    # Writable logs are operational metadata, not scenario context.
    state["stream_log"] = str(result_dir / f"{execution_id}.scenario-repair.live.ndjson")
    state["apply_stream_log"] = state["stream_log"]
    initial = state["scenario_coverage_answer"]
    receipt: dict = {
        "schema": "opsx.scenario_test_repair.live_acceptance.v1",
        "execution_id": execution_id,
        "change_id": state["change_id"],
        "repository_path": str(repo),
        "model": state["model"],
        "original_source_digest": initial["source_digest"],
        "original_plan_evidence_id": initial["plan_evidence_id"],
        "original_gap_ids": [g["scenario_id"] for g in initial["gaps"]],
        "start_at": datetime.now(timezone.utc).isoformat(),
        "repair_status": "UNKNOWN",
        "independent_verdict": "NOT_RUN",
    }
    receipt_path = result_dir / f"{execution_id}.scenario-repair-receipt.json"
    _write_receipt(receipt_path, receipt)

    repair_adapter = RepairAdapter(timeout_seconds=7200)
    try:
        repair = await ScenarioTestRepairAgent(repair_adapter).run(
            state, repository_path=repo,
        )
        receipt["repair_status"] = repair["scenario_test_repair_status"]
        receipt["repair_answer"] = repair.get("scenario_test_repair_answer")
        receipt["changed_paths"] = repair.get("scenario_test_repair_changed_paths", [])
        receipt["repair_run_id"] = repair.get("scenario_test_repair_sdk_run_id")
        if repair_adapter.last_result is not None:
            receipt["repair_usage"] = repair_adapter.last_result.usage
            receipt["repair_cost"] = repair_adapter.last_result.cost
            receipt["repair_agent_id"] = repair_adapter.last_result.agent_id
        _write_receipt(receipt_path, receipt)
        if repair["scenario_test_repair_status"] != "CANDIDATE_PENDING_VERIFY":
            pytest.fail(
                "LIVE_REPAIR_NOT_CANDIDATE: inspect machine receipt and workspace",
                pytrace=False,
            )

        # Execute the exact test nodes from the existing frozen trace matrix.
        # PostgreSQL integration tests require a separate disposable DB proof.
        post_repair_digest = input_snapshot(repo, str(state["change_id"]))["source_digest"]
        try:
            checks = asyncio.run(
                run_focused_checks(
                    repo, str(state["change_id"]), receipt["original_gap_ids"],
                    execution_id=execution_id,
                    db_proof_path=os.getenv("OPSX_SCENARIO_REPAIR_TEST_DB_PROOF_JSON"),
                ),
            )
        except LiveGateBlocked as exc:
            receipt["focused_checks"] = {"status": "BLOCKED", "reason": str(exc)}
            _write_receipt(receipt_path, receipt)
            pytest.fail(f"LIVE_FOCUSED_CHECKS_BLOCKED: {exc}", pytrace=False)
        receipt["focused_checks"] = checks
        _write_receipt(receipt_path, receipt)
        if checks["status"] not in ("PASS", "DEFERRED"):
            pytest.fail(
                "LIVE_FOCUSED_CHECKS_UNEXPECTED: repair-hop must not run pytest", pytrace=False,
            )
        if input_snapshot(repo, str(state["change_id"]))["source_digest"] != post_repair_digest:
            pytest.fail(
                "LIVE_CHECKS_MODIFIED_SOURCE: cannot accept a changed revision",
                pytrace=False,
            )

        # A separate fresh read-only model session must evaluate the changed
        # tests at the new input revision; the repair model cannot vote itself PASS.
        review_state = {
            **state,
            # Preserve original Verify context. The re-audit builds its own
            # current source snapshot and independent 70-row ledger.
            "stream_log": str(result_dir / f"{execution_id}.independent-audit.live.ndjson"),
        }
        review_adapter = CursorSDKAdapter(timeout_seconds=7200)
        review = await ScenarioCoverageVerifyAgent(review_adapter).run(review_state)
        receipt["independent_verdict"] = review["scenario_coverage_verdict"]
        receipt["independent_audit_answer"] = review["scenario_coverage_answer"]
        if review_adapter.last_result is not None:
            receipt["independent_review_agent_id"] = review_adapter.last_result.agent_id
            receipt["independent_review_run_id"] = review_adapter.last_result.run_id
            receipt["independent_review_usage"] = review_adapter.last_result.usage
            receipt["independent_review_cost"] = review_adapter.last_result.cost
        _write_receipt(receipt_path, receipt)

        if review["scenario_coverage_verdict"] != "SCENARIO_TEST_COVERAGE_PASS":
            pytest.fail(
                "LIVE_INDEPENDENT_REVIEW_NOT_PASS: see audit receipt; do not retry automatically",
                pytrace=False,
            )
        # A model-based re-audit is only one gate. Execution/DB checks and
        # additional semantic acceptance are still required for production PASS.
        receipt["status"] = "MODEL_REAUDIT_PASS__EXECUTION_TESTS_NOT_PROVEN"
        _write_receipt(receipt_path, receipt)
    except BaseException as exc:
        receipt["exception_type"] = type(exc).__name__
        receipt["finish_at"] = datetime.now(timezone.utc).isoformat()
        _write_receipt(receipt_path, receipt)
        raise
