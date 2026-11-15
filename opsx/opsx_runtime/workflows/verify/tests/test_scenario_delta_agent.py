"""Real scenario-coverage agent integration tests with independent SDK stubs.

Verify row-by-row storage, deterministic cache selection, full aggregate gate,
source freshness, no-progress and sibling regression without a live SDK.
"""
from __future__ import annotations

import json

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.verify.scenario_coverage_verify import agent as audit, semantic_validation
from agents.verify.scenario_coverage_verify import row_receipts, row_single
from agents.verify.scenario_coverage_verify.row_receipts import row_check
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
    ScenarioCoverageVerifyModelAnswer,
    bind_scenario_coverage_authority,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)


def _workspace(root, monkeypatch):
    change = root / "openspec/changes/demo/scenario-coverage"
    change.mkdir(parents=True)
    ensure_machine_receipt_dirs(change)
    (change / "plan.md").write_text(
        "- SCENARIO_ID: a\n- SCENARIO_ID: b\n", encoding="utf-8",
    )
    plan_receipt_path(change).write_text(
        '{"PLAN_EVIDENCE_ID":"frozen"}', encoding="utf-8",
    )
    trace_matrix_path(change).write_text(json.dumps({
        "entries": [
            {"scenario_id": sid, "test_node_id": f"tests/test_{sid}.py::test_{sid}"}
            for sid in ("a", "b")
        ],
    }), encoding="utf-8")
    spec = root / "openspec/changes/demo/specs"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("specification", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    for sid in ("a", "b"):
        (tests / f"test_{sid}.py").write_text(
            f"def test_{sid}():\n    assert True\n", encoding="utf-8",
        )
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "app.py").write_text("def f(): return 1\n", encoding="utf-8")
    mapping = {
        "a": "tests/test_a.py::test_a",
        "b": "tests/test_b.py::test_b",
    }
    monkeypatch.setattr(
        audit, "load_scenario_coverage_plan_bundle",
        lambda *args, **kwargs: {
            "plan_text": "- SCENARIO_ID: a\n- SCENARIO_ID: b\n",
            "plan_receipt": {"PLAN_EVIDENCE_ID": "frozen"},
        },
    )
    monkeypatch.setattr(audit, "assess_plan_materialization", lambda *_: None)
    monkeypatch.setattr(
        audit, "scenario_verifier_contract_digest", lambda *_: "test-verifier-v1",
    )
    monkeypatch.setattr(audit, "trace_mappings", lambda *_: mapping)
    monkeypatch.setattr(audit, "spec_clauses", lambda *args: (["WHEN"], ["THEN"]))
    for module in (audit, semantic_validation, row_single, row_receipts):
        monkeypatch.setattr(
            module, "review_proof_error", lambda *args, **kwargs: None,
        )
    monkeypatch.setattr(
        row_single, "get_delta_prompt",
        lambda **kwargs: kwargs["evidence_ledger"][0]["scenario_id"],
    )
    return mapping


def _answer(root, sid, mapping, assessment, *, override_rows=None):
    row = ScenarioCoverageRowReview(
        scenario_id=sid, test_node_id=mapping[sid],
        assessment=assessment, evidence=f"checked source {sid}",
        when_proofs=[1] if assessment == "SUPPORTED" else [],
        outcome_proofs=[2] if assessment == "SUPPORTED" else [],
    )
    gaps = [ScenarioCoverageGap(
        scenario_id=sid, gap_class="MISSING_TEST", evidence="missing THEN proof",
    )] if assessment == "GAP" else []
    model = ScenarioCoverageVerifyModelAnswer(
        verdict=(
            "SCENARIO_TEST_COVERAGE_PASS" if assessment == "SUPPORTED"
            else "SCENARIO_TEST_COVERAGE_FAIL" if assessment == "GAP"
            else "SCENARIO_TEST_COVERAGE_UNKNOWN"
        ),
        row_reviews=[row] if override_rows is None else override_rows,
        gaps=gaps, block_reason=(
            "cannot inspect source" if assessment == "INSPECTION_BLOCKED" else ""
        ),
        summary="independent row",
        local_hop_copy_block="one scenario",
    )
    digest = audit.input_snapshot(root, "demo")["source_digest"]
    return model, bind_scenario_coverage_authority(
        model, plan_evidence_id="frozen", source_digest=digest,
    )


class Adapter:
    def __init__(self, root, mapping, *, decisions=None, missing=(), mutate=None):
        self.root = root
        self.mapping = mapping
        self.decisions = {k: list(v) for k, v in (decisions or {}).items()}
        self.missing = set(missing)
        self.mutate = mutate
        self.prompts = []

    async def run(self, state, *, prompt, **kwargs):
        self.prompts.append(prompt)
        sid = prompt
        assessment = (
            self.decisions.get(sid, []).pop(0)
            if self.decisions.get(sid) else "SUPPORTED"
        )
        model, _bound = _answer(
            self.root, sid, self.mapping, assessment,
            override_rows=[] if sid in self.missing else None,
        )
        if self.mutate is not None:
            self.mutate(self.root)
        n = len(self.prompts)
        return AgentResult(
            text=model.model_dump_json(), parsed=model, status="finished",
            run_id=f"run-{n}", agent_id=f"agent-{n}",
        )


def _state(root, execution, **other):
    return {
        "repository_path": str(root), "change_id": "demo",
        "run_uuid": execution, "model": {"id": "composer-2.5", "options": []},
        **other,
    }


@pytest.mark.asyncio
async def test_fail_delta_and_zero_sdk_recheck(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP", "SUPPORTED"]})
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    first = await agent.run(_state(tmp_path, "exec-1"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert first["scenario_coverage_receipt"]["individual_receipts_saved"] == 1
    assert first["scenario_coverage_receipt"]["row_observations_saved"] == 2
    assert set(adapter.prompts) == {"a", "b"}

    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert 42\n", encoding="utf-8",
    )
    second = await agent.run(_state(tmp_path, "exec-2"))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 1
    assert second["scenario_coverage_receipt"]["fresh_rows"] == 1
    assert second["scenario_coverage_execution_status"] == "ROW_QUEUE_AGGREGATED"
    assert adapter.prompts.count("a") == 2
    assert adapter.prompts.count("b") == 1
    assert second["scenario_coverage_sdk_calls"] == 1
    assert second["scenario_coverage_agent_id"] is None

    third = await agent.run(_state(tmp_path, "exec-3"))
    assert third["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert third["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert third["scenario_coverage_receipt"]["reused_rows"] == 2
    assert len(adapter.prompts) == 3
    assert [r["scenario_id"] for r in second["scenario_coverage_answer"]["row_reviews"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_production_edit_does_not_invalidate_coverage_row_receipts(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    await agent.run(_state(tmp_path, "exec-1"))
    (tmp_path / "src/stroy/app.py").write_text(
        "def f(): return 2\n", encoding="utf-8",
    )
    second = await agent.run(_state(tmp_path, "exec-2"))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2
    assert second["scenario_coverage_sdk_calls"] == 0
    assert adapter.prompts.count("a") == 1
    assert adapter.prompts.count("b") == 1



@pytest.mark.asyncio
async def test_global_plan_revision_change_reuses_verified_rows_without_model(
    tmp_path, monkeypatch,
):
    _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, {"a": "tests/test_a.py::test_a",
                                 "b": "tests/test_b.py::test_b"},
                      decisions={"a": ["GAP"]})
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    first = await agent.run(_state(tmp_path, "exec-first"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert len(adapter.prompts) == 2

    (plan_receipt_path(tmp_path / "openspec/changes/demo/scenario-coverage")).write_text(
        '{"PLAN_EVIDENCE_ID":"revised-plan-id"}', encoding="utf-8",
    )
    monkeypatch.setattr(
        audit, "load_scenario_coverage_plan_bundle", lambda *args, **kwargs: {
            "plan_text": "- SCENARIO_ID: a\n- SCENARIO_ID: b\n",
            "plan_receipt": {"PLAN_EVIDENCE_ID": "revised-plan-id"},
        },
    )
    second = await agent.run(_state(tmp_path, "exec-second"))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2
    assert second["scenario_coverage_sdk_calls"] == 0
    assert len(adapter.prompts) == 2
    assert second["scenario_coverage_answer"]["plan_evidence_id"] == "revised-plan-id"


@pytest.mark.asyncio
async def test_changed_only_one_scenario_plan_entry_queues_only_that_scenario(
    tmp_path, monkeypatch,
):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP", "SUPPORTED"]})
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    await agent.run(_state(tmp_path, "exec-first"))
    # v3 row identity: mapped isolated test bytes, not plan.md metadata alone.
    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert 42\n", encoding="utf-8",
    )
    result = await agent.run(_state(tmp_path, "exec-second"))
    assert adapter.prompts.count("a") == 2
    assert adapter.prompts.count("b") == 1
    assert result["scenario_coverage_receipt"]["reused_rows"] == 1
    assert result["scenario_coverage_sdk_calls"] == 1



@pytest.mark.asyncio
async def test_incomplete_single_sdk_row_is_never_aggregate_pass(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping, missing=("a",))
    answer = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "exec-1"),
    )
    assert answer["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "SCENARIO_ROW_QUEUE_INCOMPLETE" in answer["scenario_coverage_answer"]["block_reason"]
    assert not (tmp_path / "opsx/receipts/demo/check-receipts"/
                (row_check("a") + ".json")).exists()
    # A different worker can still checkpoint row b.
    assert (tmp_path / "opsx/receipts/demo/check-receipts"/
            (row_check("b") + ".json")).exists()


@pytest.mark.asyncio
async def test_blocked_row_does_not_invalidate_supported_sibling(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(
        tmp_path, mapping, decisions={"a": ["INSPECTION_BLOCKED"]},
    )
    patch = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "exec-1"),
    )
    assert patch["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert patch["scenario_coverage_receipt"]["individual_receipts_saved"] == 1
    assert (tmp_path / "opsx/receipts/demo/check-receipts"/
            (row_check("b") + ".json")).exists()
    assert not (tmp_path / "opsx/receipts/demo/check-receipts"/
                (row_check("a") + ".json")).exists()


@pytest.mark.asyncio
async def test_tampered_one_row_only_invalidates_it(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping)
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    first = await agent.run(_state(tmp_path, "exec-1"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    receipt_path = (tmp_path / "opsx/receipts/demo/check-receipts"
                    / (row_check("b") + ".json"))
    body = json.loads(receipt_path.read_text(encoding="utf-8"))
    body["result"]["row"]["evidence"] = "tampered"
    receipt_path.write_text(json.dumps(body), encoding="utf-8")
    second = await agent.run(_state(tmp_path, "exec-2"))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert adapter.prompts.count("a") == 1
    assert adapter.prompts.count("b") == 2
    assert second["scenario_coverage_receipt"]["reused_rows"] == 1


@pytest.mark.asyncio
async def test_source_mutation_during_sdk_cannot_publish_pass(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    def mutate(root):
        (root / "tests/test_a.py").write_text(
            "def test_a():\n    assert False\n", encoding="utf-8",
        )
    adapter = Adapter(tmp_path, mapping, mutate=mutate)
    patch = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "exec-1"),
    )
    assert patch["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "SCENARIO_ROW_QUEUE_INCOMPLETE" in patch["scenario_coverage_answer"]["block_reason"]
    assert not list((tmp_path / "opsx/receipts/demo/check-receipts").glob("scenario_supported_*.json"))


@pytest.mark.asyncio
async def test_same_gap_after_apply_is_nonconvergent(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP", "GAP"]})
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    first = await agent.run(_state(tmp_path, "exec-1"))
    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert 42\n", encoding="utf-8",
    )
    second = await agent.run(_state(
        tmp_path, "exec-1",
        verify_scenario_episode_used_apply=True,
        verify_scenario_apply_run_uuid="exec-1",
        scenario_coverage_answer=first["scenario_coverage_answer"],
    ))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "AUDIT_NON_CONVERGENT" in second["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_new_sibling_gap_after_two_mapped_test_changes_is_regression(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(
        tmp_path, mapping,
        decisions={"a": ["GAP", "GAP"], "b": ["SUPPORTED", "GAP"]},
    )
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    first = await agent.run(_state(tmp_path, "exec-1"))
    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert 3\n", encoding="utf-8",
    )
    (tmp_path / "tests/test_b.py").write_text(
        "def test_b():\n    assert 3\n", encoding="utf-8",
    )
    second = await agent.run(_state(
        tmp_path, "exec-1",
        verify_scenario_episode_used_apply=True,
        verify_scenario_apply_run_uuid="exec-1",
        scenario_coverage_answer=first["scenario_coverage_answer"],
    ))
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "REGRESSION" in second["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_opsx_only_edits_do_not_rerun_scenario_agents(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    first_adapter = Adapter(tmp_path, mapping)
    agent = audit.ScenarioCoverageVerifyAgent(first_adapter)
    first = await agent.run(_state(tmp_path, "exec-v1"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert len(first_adapter.prompts) == 2

    # Neither the product snapshot nor row-level proof identity changes.
    product_digest = audit.input_snapshot(tmp_path, "demo")["source_digest"]
    runtime = tmp_path / "opsx/opsx_runtime/workflows/verify/checkpoint_resume.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("new orchestration code\\n", encoding="utf-8")
    runtime_test = tmp_path / "opsx/opsx_runtime/workflows/verify/tests/test_flow.py"
    runtime_test.parent.mkdir(parents=True)
    runtime_test.write_text("new OPSX test\\n", encoding="utf-8")
    assert audit.input_snapshot(tmp_path, "demo")["source_digest"] == product_digest

    second_adapter = Adapter(tmp_path, mapping)
    second = await audit.ScenarioCoverageVerifyAgent(second_adapter).run(
        _state(tmp_path, "exec-v2")
    )
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert second["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert second_adapter.prompts == []


@pytest.mark.asyncio
async def test_gap_reused_on_restart_with_zero_sdk_calls(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    first_adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    first = await audit.ScenarioCoverageVerifyAgent(first_adapter).run(
        _state(tmp_path, "execution-1"),
    )
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert first_adapter.prompts == ["a", "b"]

    new_adapter = Adapter(tmp_path, mapping, decisions={"a": ["SUPPORTED"], "b": ["GAP"]})
    second = await audit.ScenarioCoverageVerifyAgent(new_adapter).run(
        _state(tmp_path, "execution-2"),
    )
    assert new_adapter.prompts == []
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_sdk_calls"] == 0
    assert second["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2
    assert second["scenario_coverage_answer"]["gaps"] == first["scenario_coverage_answer"]["gaps"]
    assert [r["assessment"] for r in second["scenario_coverage_answer"]["row_reviews"]] == [
        "GAP", "SUPPORTED",
    ]


@pytest.mark.asyncio
async def test_blocked_reused_on_restart_without_false_pass(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    first = await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["INSPECTION_BLOCKED"]}),
    ).run(_state(tmp_path, "execution-1"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    new_adapter = Adapter(tmp_path, mapping, decisions={"a": ["SUPPORTED"]})
    second = await audit.ScenarioCoverageVerifyAgent(new_adapter).run(
        _state(tmp_path, "execution-2"),
    )
    assert new_adapter.prompts == []
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert second["scenario_coverage_answer"]["block_reason"] == "a: cannot inspect source"
    assert second["scenario_coverage_sdk_calls"] == 0


@pytest.mark.asyncio
async def test_gap_reuse_survives_unrelated_leaf_edit(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    (tmp_path / "tests/test_b.py").write_text(
        "def test_b():\n    assert 42\n", encoding="utf-8",
    )
    adapter = Adapter(tmp_path, mapping, decisions={"a": ["SUPPORTED"]})
    second = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "execution-2"),
    )
    assert adapter.prompts == ["b"]
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 1
    assert second["scenario_coverage_answer"]["gaps"][0]["scenario_id"] == "a"


@pytest.mark.asyncio
async def test_multi_gap_findings_survive_restart(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)

    class MultiGap(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            if prompt != "a":
                return await super().run(state, prompt=prompt, **kwargs)
            self.prompts.append(prompt)
            model, _bound = _answer(self.root, prompt, self.mapping, "GAP")
            model.gaps = [
                ScenarioCoverageGap(scenario_id="a", gap_class="WHEN", evidence="missing setup"),
                ScenarioCoverageGap(scenario_id="a", gap_class="THEN", evidence="missing assertion"),
            ]
            return AgentResult(
                text=model.model_dump_json(), parsed=model,
                status="finished", run_id="run-multi", agent_id="agent-multi",
            )

    original = await audit.ScenarioCoverageVerifyAgent(
        MultiGap(tmp_path, mapping),
    ).run(_state(tmp_path, "execution-1"))
    assert len(original["scenario_coverage_answer"]["gaps"]) == 2
    retry = Adapter(tmp_path, mapping, decisions={"a": ["SUPPORTED"]})
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(tmp_path, "execution-2"),
    )
    assert retry.prompts == []
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_answer"]["gaps"] == original["scenario_coverage_answer"]["gaps"]


@pytest.mark.asyncio
async def test_corrupt_negative_receipt_rechecks_only_corrupt_row(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    path = (
        tmp_path / "opsx/receipts/demo/scenario-rows"
        / (row_receipts.row_key("a") + ".json")
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gaps"][0]["evidence"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    retry = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(tmp_path, "execution-2"),
    )
    assert retry.prompts == ["a"]
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 1


@pytest.mark.asyncio
async def test_model_tier_change_invalidates_negative_and_positive(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    retry = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(tmp_path, "execution-2", model={"id": "different-model", "options": []}),
    )
    assert retry.prompts == []
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2


@pytest.mark.asyncio
async def test_same_gap_after_apply_stops_without_sdk_replay(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    first = await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    retry = Adapter(tmp_path, mapping)
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(
            tmp_path, "execution-1",
            verify_scenario_episode_used_apply=True,
            verify_scenario_apply_run_uuid="execution-1",
            scenario_coverage_answer=first["scenario_coverage_answer"],
        ),
    )
    assert retry.prompts == []
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "AUDIT_NON_CONVERGENT" in second["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_missing_mapped_test_gap_reuses_until_that_file_appears(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    # Missing mapped test: v3 re-queues row a (MAPPED_TEST_MISSING) until the file exists.
    (tmp_path / "tests/test_a.py").unlink()
    first = await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    retry = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(tmp_path, "execution-2"),
    )
    assert retry.prompts == ["a"]
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    (tmp_path / "tests/test_a.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8",
    )
    third_adapter = Adapter(tmp_path, mapping)
    third = await audit.ScenarioCoverageVerifyAgent(third_adapter).run(
        _state(tmp_path, "execution-3"),
    )
    assert third_adapter.prompts == ["a"]
    assert third["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert third["scenario_coverage_receipt"]["reused_rows"] == 1
    assert third["scenario_coverage_sdk_calls"] == 1
    assert third["scenario_coverage_model_dispatches"] == 1


@pytest.mark.asyncio
async def test_verifier_contract_change_invalidates_cached_negative(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    current = {"digest": "policy-v1"}
    monkeypatch.setattr(
        audit, "scenario_verifier_contract_digest",
        lambda *_: current["digest"],
    )
    await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "execution-1"))
    current["digest"] = "policy-v2"
    retry = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    second = await audit.ScenarioCoverageVerifyAgent(retry).run(
        _state(tmp_path, "execution-2"),
    )
    assert retry.prompts == []
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2


@pytest.mark.asyncio
async def test_existing_supported_and_gap_receipts_revalidated_without_model(tmp_path, monkeypatch, capsys):
    """Historical rows survive the reviewer storage redesign without model dispatch."""
    from agents.verify.scenario_coverage_verify import legacy_reuse
    from agents.verify.scenario_coverage_verify.delta_rows import impact_scope_v1
    from agents.verify.scenario_coverage_verify.row_receipts import (
        row_key, save_supported_row, write_observation,
    )

    mapping = _workspace(tmp_path, monkeypatch)
    model = {"id": "composer-2.5", "options": []}
    monkeypatch.setattr(legacy_reuse, "_review_semantics_same", lambda *_: True)
    monkeypatch.setattr(legacy_reuse, "_old_authority_digest", lambda *_: "old-verifier")
    monkeypatch.setattr(legacy_reuse.ev, "review_proof_error", lambda *_args, **_kw: None)

    old_paths = legacy_reuse.ev._legacy_paths(
        tmp_path, tmp_path / "openspec/changes/demo",
    )
    scope = impact_scope_v1(
        tmp_path, old_paths, mapping,
        authority={
            "model": model, "plan_evidence_id": "frozen",
            "verifier_contract_digest": "old-verifier",
        },
    )
    source = legacy_reuse.ev.legacy_source_digest(tmp_path, "demo")
    for sid in mapping:
        assessment = "SUPPORTED" if sid == "a" else "GAP"
        _model, answer = _answer(tmp_path, sid, mapping, assessment)
        row = answer.row_reviews[0]
        write_observation(
            tmp_path, change_id="demo", row=row,
            row_digest=scope["row_digests"][sid], source_digest=source,
            plan_evidence_id="frozen", origin_execution_id="exec-old",
            evidence_id="run-old:agent-old", gaps=answer.gaps,
        )
        if assessment == "SUPPORTED":
            assert save_supported_row(
                tmp_path, change_id="demo", row=row, mapping=mapping,
                row_digest=scope["row_digests"][sid], plan_evidence_id="frozen",
                origin_execution_id="exec-old", evidence_id="run-old:agent-old",
            )
        meta = (
            tmp_path / "opsx/opsx_runtime/run_dialogs/demo/exec-old"
            / ("scenario_coverage_verify-" + row_key(sid)) / "meta.json"
        )
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({
            "sdk_status": "finished", "run_uuid": "exec-old", "change_id": "demo",
            "agent": meta.parent.name, "model_id": "composer-2.5",
            "sdk_run_id": "run-old", "agent_id": "agent-old",
        }), encoding="utf-8")

    adapter = Adapter(tmp_path, mapping, decisions={"b": ["SUPPORTED"]})
    patch = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "exec-new"),
    )
    assert adapter.prompts == []
    assert patch["scenario_coverage_sdk_calls"] == 0
    assert patch["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert patch["scenario_coverage_receipt"]["reused_rows"] == 2
    assert patch["scenario_coverage_answer"]["gaps"][0]["scenario_id"] == "b"
    historical_logs = [
        line for line in capsys.readouterr().err.splitlines()
        if " ROW_DECISION " in line
    ]
    assert len(historical_logs) == 2
    assert all('route="LEGACY_REATTESTED"' in line for line in historical_logs)
    assert patch["scenario_coverage_model_dispatches"] == 0

    # Tampering with terminal metadata must invalidate only that row.
    bad_meta = (
        tmp_path / "opsx/opsx_runtime/run_dialogs/demo/exec-old"
        / ("scenario_coverage_verify-" + row_key("b")) / "meta.json"
    )
    tampered = json.loads(bad_meta.read_text(encoding="utf-8"))
    tampered["agent_id"] = "different-agent"
    bad_meta.write_text(json.dumps(tampered), encoding="utf-8")
    new_adapter = Adapter(tmp_path, mapping, decisions={"b": ["GAP"]})
    new_patch = await audit.ScenarioCoverageVerifyAgent(new_adapter).run(
        _state(tmp_path, "exec-newer"),
    )
    assert new_adapter.prompts == ["b"]
    assert new_patch["scenario_coverage_receipt"]["reused_rows"] == 1


@pytest.mark.asyncio
async def test_other_openspec_change_and_unmapped_test_do_not_reaudit(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    adapter = Adapter(tmp_path, mapping)
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    before = await agent.run(_state(tmp_path, "exec-original"))
    assert before["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    digest = before["scenario_coverage_source_snapshot"]["source_digest"]

    foreign = tmp_path / "openspec/changes/foreign/specs"
    foreign.mkdir(parents=True)
    (foreign / "unrelated.md").write_text("Unrelated spec delta\n", encoding="utf-8")
    (tmp_path / "tests/test_foreign.py").write_text(
        "def test_unrelated():\n    assert False\n", encoding="utf-8",
    )
    assert audit.input_snapshot(tmp_path, "demo")["source_digest"] == digest

    new_adapter = Adapter(tmp_path, mapping)
    second = await audit.ScenarioCoverageVerifyAgent(new_adapter).run(
        _state(tmp_path, "exec-again"),
    )
    assert new_adapter.prompts == []
    assert second["scenario_coverage_execution_status"] == "REUSED_ROWS"
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2


@pytest.mark.asyncio
async def test_unmapped_test_helper_edit_does_not_invalidate_mapped_file(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, monkeypatch)
    helper = tmp_path / "tests/support/test_helper.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("def help_me(): return True\n", encoding="utf-8")
    test_a = tmp_path / "tests/test_a.py"
    test_a.write_text(
        "from tests.support.test_helper import help_me\n"
        "def test_a():\n    assert help_me()\n",
        encoding="utf-8",
    )
    adapter = Adapter(tmp_path, mapping)
    agent = audit.ScenarioCoverageVerifyAgent(adapter)
    await agent.run(_state(tmp_path, "exec-initial"))
    helper.write_text("def help_me(): return False\n", encoding="utf-8")
    second = await agent.run(_state(tmp_path, "exec-after-helper-edit"))
    assert adapter.prompts.count("a") == 1
    assert adapter.prompts.count("b") == 1
    assert second["scenario_coverage_receipt"]["reused_rows"] == 2
    assert second["scenario_coverage_sdk_calls"] == 0


@pytest.mark.asyncio
async def test_row_decision_logs_distinguish_skip_queue_and_actual_model_dispatch(
    tmp_path, monkeypatch, capsys,
):
    mapping = _workspace(tmp_path, monkeypatch)
    first_adapter = Adapter(tmp_path, mapping, decisions={"a": ["GAP"]})
    await audit.ScenarioCoverageVerifyAgent(first_adapter).run(
        _state(tmp_path, "exec-initial"),
    )
    capsys.readouterr()

    # Editing only a mapped isolated test invalidates exactly its row.
    (tmp_path / "tests/test_b.py").write_text(
        "def test_b():\n    assert 42\n", encoding="utf-8",
    )
    repeat_adapter = Adapter(tmp_path, mapping)
    patch = await audit.ScenarioCoverageVerifyAgent(repeat_adapter).run(
        _state(tmp_path, "exec-repeat"),
    )
    lines = capsys.readouterr().err.splitlines()
    decisions = [line for line in lines if " ROW_DECISION " in line]
    dispatches = [line for line in lines if " MODEL_DISPATCH " in line]

    assert len(decisions) == 2
    assert 'scenario_id="a"' in decisions[0]
    assert 'decision="REUSE"' in decisions[0]
    assert 'skip_model=true' in decisions[0]
    assert 'route="CURRENT_NEGATIVE_OBSERVATION"' in decisions[0]
    assert 'scenario_id="b"' in decisions[1]
    assert 'decision="MODEL_QUEUED"' in decisions[1]
    assert 'skip_model=false' in decisions[1]
    assert 'reason="SPEC_OR_TEST_CHANGED"' in decisions[1]
    assert len(dispatches) == 1 and 'scenario_id="b"' in dispatches[0]
    assert len(repeat_adapter.prompts) == 1 and repeat_adapter.prompts == ["b"]
    summary = [line for line in lines if " SUMMARY " in line]
    assert len(summary) == 1
    assert 'reused=1' in summary[0]
    assert 'model_queued=1' in summary[0]
    assert 'model_dispatches=1' in summary[0]
    assert patch["scenario_coverage_model_dispatches"] == 1
    assert [row["decision"] for row in patch["scenario_coverage_selection"]] == [
        "REUSE", "MODEL_QUEUED",
    ]


@pytest.mark.asyncio
async def test_zero_dispatch_restart_has_per_row_reuse_logs_and_summary(
    tmp_path, monkeypatch, capsys,
):
    mapping = _workspace(tmp_path, monkeypatch)
    await audit.ScenarioCoverageVerifyAgent(
        Adapter(tmp_path, mapping, decisions={"a": ["GAP"]}),
    ).run(_state(tmp_path, "exec-old"))
    capsys.readouterr()

    adapter = Adapter(tmp_path, mapping)
    patch = await audit.ScenarioCoverageVerifyAgent(adapter).run(
        _state(tmp_path, "exec-new"),
    )
    lines = capsys.readouterr().err.splitlines()
    decisions = [line for line in lines if " ROW_DECISION " in line]
    assert len(decisions) == 2
    assert all('decision="REUSE"' in line and 'skip_model=true' in line for line in decisions)
    assert not any(" MODEL_DISPATCH " in line for line in lines)
    summary = [line for line in lines if " SUMMARY " in line]
    assert len(summary) == 1
    assert 'model_queued=0' in summary[0]
    assert 'model_dispatches=0' in summary[0]
    assert adapter.prompts == []
    assert patch["scenario_coverage_model_dispatches"] == 0
    assert patch["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"


def test_row_decision_telemetry_is_part_of_durable_graph_state():
    from agents.state import GraphState

    for key in (
        "scenario_coverage_selection",
        "scenario_coverage_model_dispatches",
        "scenario_coverage_sdk_calls",
    ):
        assert key in GraphState.__annotations__, f"{key} not checkpoint-persistable"
