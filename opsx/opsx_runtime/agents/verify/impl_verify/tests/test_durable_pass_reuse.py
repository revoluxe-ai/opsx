"""PHASE_IMPL LLM clean verdict is reused only with fresh verified preflight."""
from __future__ import annotations

import json
import shutil
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.impl_verify.schemas import VerifyImplAnswer
from agents.check_receipts import write_pass
from agents.verify.check_reuse import review_receipt_key


def _workspace(root):
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "design.md").write_text("behavior contract\n", encoding="utf-8")
    source = root / "src/stroy"
    source.mkdir(parents=True)
    (source / "service.py").write_text("def f(): return 1\n", encoding="utf-8")


def _preflight(stale="PASS"):
    return {
        "config_contract": {"status": "PASS"},
        "apply_completion_status": "IMPL_OK",
        "stale_green_refresh": {"status": stale},
    }


def _answer():
    return VerifyImplAnswer(
        verdict="IMPL_OK", behavior_summary="Implemented",
        architecture_placement="correct", tasks_consistency="correct",
        gap_details=[], next_command="",
        local_hop_copy_block="Workspace: demo\nVerdict: IMPL_OK",
    )


@pytest.mark.asyncio
async def test_second_execution_does_not_resend_impl_model(tmp_path, monkeypatch):
    _workspace(tmp_path)
    module = import_module("agents.verify.impl_verify.agent")
    monkeypatch.setattr(module, "collect_deterministic_preflight", lambda _: _preflight())
    monkeypatch.setattr(module, "load_change_artifact_bundle", lambda _: {"artifacts": {}})
    monkeypatch.setattr(module, "get_prompt", lambda **kwargs: "Stable impl prompt")
    monkeypatch.setattr(module, "format_verify_impl_report", lambda **kwargs: "IMPL_OK report")
    adapter = MagicMock()
    adapter.run = AsyncMock(return_value=AgentResult(
        text=_answer().model_dump_json(), parsed=_answer(),
        run_id="sdk-a", agent_id="agent-a", status="finished",
    ))
    agent = ImplVerifyAgent(adapter)
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-a", "model": {"id": "composer-2.5"},
    }
    first = await agent.run(state)
    assert first["check_receipt"]["saved"] is True

    second = await agent.run({**state, "run_uuid": "exec-b"})
    assert second["verdict"] == "IMPL_OK"
    assert second["check_receipt"]["source"] == "durable_pass"
    assert second["check_receipt"]["origin_execution_id"] == "exec-a"
    assert second["check_receipt"]["current_execution_id"] == "exec-b"
    assert second["execution_status"] == "REUSED_PASS"
    assert second["run_id"] is None
    assert second["agent_id"] == ""
    assert adapter.run.await_count == 1

    # Reviewer code/model/prompt and workspace location are not product edits.
    policy = tmp_path / "opsx/opsx_runtime/agents/state.py"
    policy.parent.mkdir(parents=True)
    policy.write_text("new runtime state fields\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_prompt", lambda **kwargs: "Changed impl prompt")
    relocated = tmp_path.parent / (tmp_path.name + "-relocated")
    shutil.copytree(tmp_path, relocated)
    third = await agent.run({
        **state, "run_uuid": "exec-relocated", "model": {"id": "another-model"},
        "repository_path": str(relocated), "workspace": "/agent/another-workspace",
    })
    assert third["check_receipt"]["source"] == "durable_pass"
    assert third["check_receipt"]["origin_execution_id"] == "exec-a"
    assert "verifier_contract_digest" not in third["check_receipt"]
    assert adapter.run.await_count == 1
    receipt = json.loads((
        tmp_path / "opsx/receipts/demo/check-receipts/impl_semantic_review.json"
    ).read_text(encoding="utf-8"))
    assert "verifier_contract_digest" not in receipt
    assert "product_input_digest" not in receipt

    # A new normative decision must force a fresh SDK review, without a commit.
    decision = tmp_path / "openspec/changes/demo/decisions/fsm/decision.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("Use one shared FSM\n", encoding="utf-8")
    result = await agent.run({**state, "run_uuid": "exec-c"})
    assert result["check_receipt"]["source"] == "fresh_audit"
    assert adapter.run.await_count == 2

    # Changed product source also invalidates, even with exactly the same HEAD.
    (tmp_path / "src/stroy/service.py").write_text("def f(): return 2\n", encoding="utf-8")
    result = await agent.run({**state, "run_uuid": "exec-d"})
    assert result["check_receipt"]["source"] == "fresh_audit"
    assert adapter.run.await_count == 3


@pytest.mark.asyncio
async def test_partial_preflight_reuses_only_semantic_assessment(tmp_path, monkeypatch):
    _workspace(tmp_path)
    module = import_module("agents.verify.impl_verify.agent")
    monkeypatch.setattr(module, "collect_deterministic_preflight", lambda _: _preflight("PARTIAL"))
    monkeypatch.setattr(module, "load_change_artifact_bundle", lambda _: {"artifacts": {}})
    monkeypatch.setattr(module, "get_prompt", lambda **kwargs: "Stable impl prompt")
    monkeypatch.setattr(module, "format_verify_impl_report", lambda **kwargs: "IMPL_OK")
    adapter = MagicMock()
    adapter.run = AsyncMock(return_value=AgentResult(
        text=_answer().model_dump_json(), parsed=_answer(),
        run_id="sdk-a", agent_id="agent-a", status="finished",
    ))
    agent = ImplVerifyAgent(adapter)
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-a", "model": {"id": "composer-2.5"},
    }
    first = await agent.run(state)
    assert first["verdict"] == "IMPL_OK"
    assert first["check_receipt"]["saved"] is True
    assert first["check_receipt"]["coverage"] == "semantic_review_only"
    second = await agent.run({**state, "run_uuid": "exec-b"})
    assert second["check_receipt"]["source"] == "durable_pass"
    assert second["check_receipt"]["preflight_status"] == "PARTIAL"
    assert second["check_receipt"]["coverage"] == "semantic_review_only"
    assert second["execution_status"] == "REUSED_SEMANTIC_ONLY"
    assert second["deterministic_preflight"]["stale_green_refresh"]["status"] == "PARTIAL"
    assert adapter.run.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "source", "checksum", "schema"])
async def test_old_reviewer_fingerprint_receipt_without_new_model_call(
    tmp_path, monkeypatch, damage,
):
    """Old IMPL_OK survives reviewer changes; input/schema/checksum checks stay."""
    _workspace(tmp_path)
    module = import_module("agents.verify.impl_verify.agent")
    monkeypatch.setattr(module, "collect_deterministic_preflight", lambda _: _preflight())
    monkeypatch.setattr(module, "load_change_artifact_bundle", lambda _: {"artifacts": {}})
    monkeypatch.setattr(module, "get_prompt", lambda **kwargs: "New reviewer prompt")
    monkeypatch.setattr(module, "format_verify_impl_report", lambda **kwargs: "IMPL_OK")
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "new-execution", "model": {"id": "different-model"},
    }
    key = review_receipt_key(state, role="impl_verify", prompt="historical prompt")
    result = _answer().model_dump(mode="json")
    if damage == "schema":
        result.pop("behavior_summary")
    write_pass(
        tmp_path, change_id="demo", check="impl_semantic_review",
        contract="read_only_review_v2", input_digest=key.digest,
        verifier_contract_digest="old-reviewer-fingerprint",
        origin_execution_id="original-execution", evidence_id="old-run:old-agent",
        pass_verdict="IMPL_OK", result=result,
    )
    path = tmp_path / "opsx/receipts/demo/check-receipts/impl_semantic_review.json"
    original = path.read_bytes()
    if damage == "source":
        (tmp_path / "src/stroy/service.py").write_text("def f(): return 2\n", encoding="utf-8")
    elif damage == "checksum":
        payload = json.loads(original)
        payload["checksum"] = "bad"
        path.write_text(json.dumps(payload), encoding="utf-8")
    adapter = MagicMock()
    adapter.run = AsyncMock(return_value=AgentResult(
        text=_answer().model_dump_json(), parsed=_answer(),
        run_id="new-run", agent_id="new-agent", status="finished",
    ))
    for execution in ("new-execution", "second-restart"):
        answer = await ImplVerifyAgent(adapter).run({**state, "run_uuid": execution})
        assert answer["verdict"] == "IMPL_OK"
    if damage is None:
        adapter.run.assert_not_awaited()
        assert answer["check_receipt"]["origin_execution_id"] == "original-execution"
        assert path.read_bytes() == original
    else:
        # Rejected legacy evidence is reviewed once; the new result then reuses.
        assert adapter.run.await_count == 1

