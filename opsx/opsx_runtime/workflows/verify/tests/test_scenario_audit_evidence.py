"""Regression tests: source-bound evidence, contradiction and fail-closed audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.common.scenario_files import materialize_scenario_files
from agents.verify.scenario_coverage_verify.agent import (
    _audit_completeness_error,
    _audit_proof_error,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    review_proof_error,
    spec_clauses,
    trace_mappings,
)
from agents.verify.scenario_coverage_verify.mapped_test_view import enrich_ledger_mapped_test
from agents.verify.scenario_coverage_verify.prompt.get_prompt import get_prompt, get_delta_prompt
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
    model_answer_from_durable,
)




@pytest.fixture(autouse=True)
def _bind_synthetic_scenario_verifier_policy(monkeypatch):
    # Synthetic repositories do not vendor the OPSX runtime; bind policy
    # explicitly rather than turning an unrelated fixture into an UNKNOWN.
    monkeypatch.setattr(
        "agents.verify.scenario_coverage_verify.agent.scenario_verifier_contract_digest",
        lambda *_args: "synthetic-scenario-verifier-v1",
    )


def _fixture(root: Path) -> tuple[str, str, dict]:
    change_id = "demo"
    sid = "openspec/changes/demo/specs/core/spec.md::Intent audit"
    node = "tests/test_scenario.py::test_alpha"
    spec = root / "openspec/changes/demo/specs/core/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### Requirement: audited intent\n"
        "#### Scenario: Intent audit\n"
        "- **WHEN** the event is processed\n"
        "- **THEN** a result is stored\n"
        "- **AND** an audit reason is recorded\n",
        encoding="utf-8",
    )
    plans = root / "openspec/changes/demo/scenario-coverage"
    plans.mkdir(parents=True)
    ensure_machine_receipt_dirs(plans)
    plan = f"SCENARIO_COVERAGE_PLAN:\n  SCENARIOS:\n    - SCENARIO_ID: {sid}\n"
    (plans / "plan.md").write_text(plan, encoding="utf-8")
    plan_receipt_path(plans).write_text(
        json.dumps({"PLAN_EVIDENCE_ID": "evidence-id", "SCENARIO_COVERAGE_RECEIPT": True, "PLAN_STATUS": "ready"}),
        encoding="utf-8",
    )
    materialize_scenario_files(plans)
    trace_matrix_path(plans).write_text(
        json.dumps({"entries": [{"scenario_id": sid, "test_node_id": node}]}),
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_scenario.py").write_text(
        "def test_alpha():\n"
        "    event = True\n"
        "    result = 'ok'\n"
        "    reason = 'allowed'\n"
        "    assert result == 'ok'\n"
        "    assert reason == 'allowed'\n",
        encoding="utf-8",
    )
    return sid, node, {
        "plan_text": plan,
        "plan_receipt": {"PLAN_EVIDENCE_ID": "evidence-id"},
    }


def _row(sid: str, node: str, *, when=(2,), then=(5, 6)):
    return ScenarioCoverageRowReview(
        scenario_id=sid, test_node_id=node, assessment="SUPPORTED",
        evidence="Verified result and reason assertions",
        when_proofs=list(when), outcome_proofs=list(then),
    )


def test_frozen_clauses_preserve_when_and_then_and_order(tmp_path):
    sid, _node, _bundle = _fixture(tmp_path)
    assert spec_clauses(tmp_path, "demo", sid) == (
        ["the event is processed"],
        ["a result is stored", "an audit reason is recorded"],
    )


def test_supported_requires_source_locatable_proofs(tmp_path):
    sid, node, bundle = _fixture(tmp_path)
    with pytest.raises(ValidationError, match="SUPPORTED requires"):
        ScenarioCoverageVerifyAnswer(
            verdict="SCENARIO_TEST_COVERAGE_PASS",
            source_digest="abc", plan_evidence_id="evidence-id",
            summary="claims coverage without proof", gaps=[],
            row_reviews=[ScenarioCoverageRowReview(
                scenario_id=sid, test_node_id=node, assessment="SUPPORTED",
                evidence="test exists",
            )],
            local_hop_copy_block="test",
        )
    snapshot = input_snapshot(tmp_path, "demo")
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_PASS",
        source_digest=snapshot["source_digest"],
        plan_evidence_id="evidence-id", summary="source references validated",
        row_reviews=[_row(sid, node)], gaps=[],
        local_hop_copy_block="test",
    )
    assert _audit_completeness_error(answer, bundle) is None
    assert _audit_proof_error(
        answer, repo_root=tmp_path, change_id="demo",
        source_snapshot=snapshot, mappings=trace_mappings(tmp_path, "demo"),
    ) is None


def test_vacuous_outcome_line_cannot_be_a_proof(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    row = _row(sid, node, then=(3, 4))
    assert "PROOF_NOT_ASSERTION" in str(
        review_proof_error(tmp_path, "demo", row, expected_node=node)
    )


def test_uncompilable_future_import_is_not_a_valid_mapped_test(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    path = tmp_path / "tests" / "test_scenario.py"
    path.write_text(
        '"""doc."""\n'
        "import pytest\n"
        "from __future__ import annotations\n"
        "\n"
        "def test_alpha():\n"
        "    event = True\n"
        "    assert result == 'ok'\n"
        "    assert reason == 'because'\n",
        encoding="utf-8",
    )
    problem = review_proof_error(tmp_path, "demo", _row(sid, node), expected_node=node)
    assert problem is not None and "MAPPED_TEST_SYNTAX_ERROR" in problem
    ledger = enrich_ledger_mapped_test(tmp_path, {"test_node_id": node})
    assert "MAPPED_TEST_SYNTAX_ERROR" in str(ledger.get("mapped_test_syntax_error"))


def test_proof_outside_test_or_wrong_trace_mapping_fails_closed(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    wrong = _row(sid, node, when=(999,))
    assert "PROOF_OUTSIDE_TEST" in str(
        review_proof_error(tmp_path, "demo", wrong, expected_node=node)
    )
    assert "MAPPED_TEST_MISMATCH" in str(
        review_proof_error(
            tmp_path, "demo", _row(sid, node),
            expected_node="tests/other.py::test_other",
        )
    )


def test_revision_identity_changes_when_test_assertions_change(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    old = input_snapshot(tmp_path, "demo")
    path = tmp_path / "tests/test_scenario.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "assert reason == 'allowed'", "assert reason == 'blocked'"
        ),
        encoding="utf-8",
    )
    new = input_snapshot(tmp_path, "demo")
    assert old["source_digest"] != new["source_digest"]
    row = _row(sid, node)
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_PASS",
        source_digest=old["source_digest"], plan_evidence_id="evidence-id",
        summary="stale proof", row_reviews=[row],
        local_hop_copy_block="test",
    )
    assert "AUDIT_STALE" in str(_audit_proof_error(
        answer, repo_root=tmp_path, change_id="demo",
        source_snapshot=new, mappings=trace_mappings(tmp_path, "demo"),
    ))


def test_trace_mapping_not_reported_as_behavioral_proof(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    mapping = trace_mappings(tmp_path, "demo")
    assert mapping == {sid: node}
    assert not _row(sid, node, then=(3, 4)).outcome_proofs == [5, 6]


def test_audit_prompt_contains_source_identity_and_compact_clause_contract(tmp_path):
    sid, node, bundle = _fixture(tmp_path)
    snapshot = input_snapshot(tmp_path, "demo")
    prompt = get_prompt(
        repository_path=str(tmp_path), change_id="demo", plan_bundle=bundle,
        source_snapshot=snapshot,
        evidence_ledger=[{
            "scenario_id": sid, "test_node_id": node,
            "when_clauses": ["the event is processed"],
            "outcome_clauses": ["a result is stored", "an audit reason is recorded"],
        }],
    )
    assert snapshot["source_digest"] in prompt
    assert "outcome_proofs" in prompt
    assert "assert_outbox_enqueue_only" not in prompt  # do not hard-code one project
    assert "SOURCE SNAPSHOT" in prompt


def test_apply_rejects_stale_or_unbound_scenario_evidence(tmp_path):
    from agents.implementation.apply.agent import scenario_coverage_apply_input_error

    _sid, _node, _bundle = _fixture(tmp_path)
    assert "UNBOUND" in str(scenario_coverage_apply_input_error(
        {}, repo_root=tmp_path, change_id="demo",
    ))
    digest = input_snapshot(tmp_path, "demo")["source_digest"]
    state = {
        "scenario_coverage_source_snapshot": {"source_digest": digest},
        "scenario_coverage_answer": {"source_digest": digest},
    }
    assert scenario_coverage_apply_input_error(
        state, repo_root=tmp_path, change_id="demo",
    ) is None
    test = tmp_path / "tests" / "test_scenario.py"
    test.write_text(test.read_text(encoding="utf-8") + "\n# new revision\n", encoding="utf-8")
    assert "STALE" in str(scenario_coverage_apply_input_error(
        state, repo_root=tmp_path, change_id="demo",
    ))


def test_missing_scenario_proof_cannot_be_promoted_to_pass(tmp_path):
    sid, node, _bundle = _fixture(tmp_path)
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_FAIL",
        source_digest=input_snapshot(tmp_path, "demo")["source_digest"],
        plan_evidence_id="evidence-id", summary="found a semantic gap",
        row_reviews=[ScenarioCoverageRowReview(
            scenario_id=sid, test_node_id=node, assessment="GAP",
            evidence="test never invokes required processing seam",
        )],
        gaps=[{
            "scenario_id": sid, "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST",
            "evidence": "only webhook ingress reached",
        }],
        local_hop_copy_block="test",
    )
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_FAIL"
    assert answer.row_reviews[0].when_proofs == []


class _CountingScenarioAdapter:
    def __init__(self, answer: ScenarioCoverageVerifyAnswer):
        self.answer = answer
        self.calls = 0

    async def run(self, state, **kwargs):
        from agents.adapters.abc_adapter import AgentResult
        self.calls += 1
        model = model_answer_from_durable(self.answer)
        return AgentResult(
            text=model.model_dump_json(),
            parsed=model,
            status="finished",
            agent_id="sdk-agent-1",
            run_id=f"sdk-run-{self.calls}",
        )


def _certified_pass(tmp_path: Path):
    sid, node, _bundle = _fixture(tmp_path)
    digest = input_snapshot(tmp_path, "demo")["source_digest"]
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_PASS",
        source_digest=digest, plan_evidence_id="evidence-id",
        summary="All rows independently inspected",
        row_reviews=[_row(sid, node)], gaps=[], block_reason="",
        local_hop_copy_block="Workspace: fixture; verdict: PASS",
    )
    return answer


@pytest.mark.asyncio
async def test_scenario_pass_durable_row_receipt_avoids_second_llm_send(tmp_path):
    from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent

    answer = _certified_pass(tmp_path)
    adapter = _CountingScenarioAdapter(answer)
    agent = ScenarioCoverageVerifyAgent(adapter)
    state = {
        "change_id": "demo", "repository_path": str(tmp_path),
        "workspace": str(tmp_path), "run_uuid": "execution-1",
        "model": {"id": "composer-2.5"},
    }
    first = await agent.run(state)
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    # One row is supported, but no aggregate receipt is attributed to its SDK run.
    assert first["scenario_coverage_receipt"]["saved"] is False
    assert first["scenario_coverage_receipt"]["individual_receipts_saved"] == 1
    assert adapter.calls == 1
    from agents.check_receipts import read_pass, SCENARIO_COVERAGE_CONTRACT
    cached = read_pass(
        tmp_path, change_id="demo", check="scenario_coverage",
        contract=SCENARIO_COVERAGE_CONTRACT,
        pass_verdict="SCENARIO_TEST_COVERAGE_PASS",
        input_digest=first["scenario_coverage_source_snapshot"]["source_digest"],
    )
    assert cached is None  # No fictitious whole-plan single-agent receipt.

    second = await agent.run({**state, "run_uuid": "execution-2",
                              "model": {"id": "composer-2.5"}})
    assert adapter.calls == 1  # Identical product and verifier authority.
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert second["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert second["scenario_coverage_receipt"]["source"] == "durable_rows"
    assert second["scenario_coverage_receipt"]["current_execution_id"] == "execution-2"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 1
    assert second["scenario_coverage_receipt"]["fresh_rows"] == 0
    assert second["scenario_coverage_run_id"] is None
    assert second["scenario_coverage_agent_id"] is None
    assert second["scenario_coverage_validation_attempts"] == 0


@pytest.mark.asyncio
async def test_scenario_cache_ignores_logs_but_reaudits_changed_tests(tmp_path):
    from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent

    adapter = _CountingScenarioAdapter(_certified_pass(tmp_path))
    agent = ScenarioCoverageVerifyAgent(adapter)
    state = {
        "change_id": "demo", "repository_path": str(tmp_path),
        "run_uuid": "execution-1",
        "model": {"id": "composer-2.5"},
    }
    await agent.run(state)
    logs = tmp_path / "opsx/receipts/demo"
    (logs / "sdk.log").write_text("new log\n", encoding="utf-8")
    await agent.run({**state, "run_uuid": "execution-2"})
    assert adapter.calls == 1

    test = tmp_path / "tests/test_scenario.py"
    test.write_text(test.read_text(encoding="utf-8") + "\n# relevant source changed\n", encoding="utf-8")
    # Refresh expected snapshot without recreating/overwriting the changed test.
    adapter.answer = adapter.answer.model_copy(update={
        "source_digest": input_snapshot(tmp_path, "demo")["source_digest"],
    })
    fresh = await agent.run({**state, "run_uuid": "execution-3"})
    assert adapter.calls == 2
    assert fresh["scenario_coverage_receipt"]["source"] == "fresh_audit"


@pytest.mark.asyncio
async def test_scenario_fail_cannot_generate_pass_receipt(tmp_path):
    from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent

    sid, node, _ = _fixture(tmp_path)
    bad = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_FAIL",
        plan_evidence_id="evidence-id",
        source_digest=input_snapshot(tmp_path, "demo")["source_digest"],
        summary="Missing causal test",
        row_reviews=[ScenarioCoverageRowReview(
            scenario_id=sid, test_node_id=node, assessment="GAP",
            evidence="Missing contract assertion",
        )],
        gaps=[{
            "scenario_id": sid,
            "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST",
            "evidence": "Missing contract assertion",
        }],
        local_hop_copy_block="Workspace: fixture; verdict FAIL",
    )
    agent = ScenarioCoverageVerifyAgent(_CountingScenarioAdapter(bad))
    result = await agent.run({
        "change_id": "demo", "repository_path": str(tmp_path),
        "run_uuid": "execution-1",
        "model": {"id": "composer-2.5"},
    })
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert result["scenario_coverage_receipt"]["saved"] is False
    from agents.check_receipts import read_pass, SCENARIO_COVERAGE_CONTRACT
    assert read_pass(
        tmp_path, change_id="demo", check="scenario_coverage",
        contract=SCENARIO_COVERAGE_CONTRACT,
        pass_verdict="SCENARIO_TEST_COVERAGE_PASS",
        input_digest=input_snapshot(tmp_path, "demo")["source_digest"],
    ) is None


@pytest.mark.asyncio
async def test_damaged_aggregate_receipt_does_not_invalidate_verified_rows(tmp_path):
    from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
    adapter = _CountingScenarioAdapter(_certified_pass(tmp_path))
    agent = ScenarioCoverageVerifyAgent(adapter)
    state = {"change_id": "demo", "repository_path": str(tmp_path),
             "run_uuid": "exec-a", "model": {"id": "composer-2.5"}}
    await agent.run(state)
    receipt = tmp_path / "opsx/receipts/demo/check-receipts/scenario_coverage.json"
    receipt.write_text("{bad json", encoding="utf-8")
    result = await agent.run({**state, "run_uuid": "exec-b"})
    # The malformed aggregate is ignored; independent row PASS is still valid.
    assert adapter.calls == 1
    assert result["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert result["scenario_coverage_receipt"]["source"] == "durable_rows"
    assert result["scenario_coverage_receipt"]["reused_rows"] == 1


def test_scenario_green_bit_never_skips_receipt_validation():
    from agents.verify.graph_validation import resolve_impl_verify_route_target

    state = {
        "verdict": "IMPL_OK",
        "cycle_checks": {
            "established": {
                "scenario_coverage": "fake-green-HEAD",
            },
        },
    }
    assert resolve_impl_verify_route_target(state) == "scenario_coverage_verify"


def test_ledger_embeds_numbered_mapped_test_for_model_payload(tmp_path):
    sid, node, bundle = _fixture(tmp_path)
    snap = input_snapshot(tmp_path, "demo")
    ledger = [enrich_ledger_mapped_test(tmp_path, {
        "scenario_id": sid,
        "test_node_id": node,
        "when_clauses": ["the event is processed"],
        "outcome_clauses": ["a result is stored", "an audit reason is recorded"],
    })]
    prompt = get_delta_prompt(
        repository_path=str(tmp_path),
        change_id="demo",
        plan_evidence_id="evidence-id",
        source_snapshot=snap,
        evidence_ledger=ledger,
    )
    entry = ledger[0]
    assert "mapped_test_numbered_source" in entry
    assert "   5|     assert result == 'ok'" in entry["mapped_test_numbered_source"]
    assert entry["mapped_test_function_first_line"] == 1
    assert str(entry["mapped_test_function_last_line"]).isdigit()
    assert "mapped_test_numbered_source" in prompt
    assert "mapped_test_function_first_line" in prompt
    assert "assert result == 'ok'" in prompt


def test_prompt_and_proof_validator_agree_on_absolute_file_line_numbers(tmp_path):
    sid, node, bundle = _fixture(tmp_path)
    snap = input_snapshot(tmp_path, "demo")
    prompt = get_prompt(
        repository_path=str(tmp_path), change_id="demo",
        plan_bundle=bundle, source_snapshot=snap,
        evidence_ledger=[{
            "scenario_id": sid, "test_node_id": node,
            "when_clauses": ["the event is processed"],
            "outcome_clauses": ["a result is stored", "an audit reason is recorded"],
        }],
    )
    assert "абсолютные 1-based" in prompt
    assert "относительные номера" in prompt

    # A test starting at file line 29 must never accept function-relative
    # numbers 2, 5, 6, even when the referenced statements exist.
    path = tmp_path / "tests/test_scenario.py"
    path.write_text(("# preceding source line\n" * 28) + path.read_text(encoding="utf-8"), encoding="utf-8")
    problem = review_proof_error(tmp_path, "demo", _row(sid, node), expected_node=node)
    assert problem is not None and "PROOF_OUTSIDE_TEST" in problem


def test_scenario_verifier_identity_separates_model_and_semantic_policy(tmp_path):
    from agents.verify.scenario_coverage_verify.evidence_contract import (
        _SCENARIO_VERIFIER_FILES, scenario_verifier_contract_digest,
    )

    for rel in _SCENARIO_VERIFIER_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original semantic policy\\n", encoding="utf-8")
    model = {"id": "composer-2.5", "options": []}
    original = scenario_verifier_contract_digest(tmp_path, model)
    assert original != scenario_verifier_contract_digest(
        tmp_path, {"id": "other-tier", "options": []},
    )
    sdk_queue = tmp_path / "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_single.py"
    sdk_queue.write_text("concurrency = 1\\n", encoding="utf-8")
    assert scenario_verifier_contract_digest(tmp_path, model) == original

    semantic = tmp_path / "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/semantic_validation.py"
    semantic.write_text("changed proof contract\\n", encoding="utf-8")
    assert scenario_verifier_contract_digest(tmp_path, model) != original
