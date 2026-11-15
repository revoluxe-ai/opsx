"""Scenario row PASS reuse must be bound to every shared validation dependency."""
from __future__ import annotations

import pytest

from agents.verify.scenario_coverage_verify import evidence_contract as contract
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope


_SHARED_VALIDATION = (
    "opsx/opsx_runtime/agents/abc_agent.py",
    "opsx/opsx_runtime/agents/utils/response.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py",
)
_TRANSPORT_ONLY = (
    "opsx/opsx_runtime/agents/adapters/abc_adapter.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/agent.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_single.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_receipts.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_recovery.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/delta_rows.py",
)
_MODEL = {"id": "composer-2.5", "options": []}


def _policy_workspace(root, monkeypatch):
    # Bound minimal fixture: real function/digest computation, isolated sources.
    monkeypatch.setattr(contract, "_SCENARIO_VERIFIER_FILES", _SHARED_VALIDATION)
    for name in _SHARED_VALIDATION:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# policy {name}\n", encoding="utf-8")
    return contract.scenario_verifier_contract_digest(root, _MODEL)


def test_policy_dependency_closure_includes_validation_and_parse_paths() -> None:
    assert set(_SHARED_VALIDATION).issubset(set(contract._SCENARIO_VERIFIER_FILES))
    assert len(contract._SCENARIO_VERIFIER_FILES) == len(
        set(contract._SCENARIO_VERIFIER_FILES)
    )
    assert not set(_TRANSPORT_ONLY) & set(contract._SCENARIO_VERIFIER_FILES)


@pytest.mark.parametrize("modified", _SHARED_VALIDATION)
def test_shared_validator_changes_invalidate_policy_digest(tmp_path, monkeypatch, modified):
    baseline = _policy_workspace(tmp_path, monkeypatch)
    path = tmp_path / modified
    path.write_bytes(path.read_bytes() + b"# changed policy\n")
    changed = contract.scenario_verifier_contract_digest(tmp_path, _MODEL)
    assert changed != baseline
    path.write_bytes(path.read_bytes().replace(b"# changed policy\n", b""))
    assert contract.scenario_verifier_contract_digest(tmp_path, _MODEL) == baseline


def test_digest_binds_model_and_options(tmp_path, monkeypatch):
    baseline = _policy_workspace(tmp_path, monkeypatch)
    assert baseline != contract.scenario_verifier_contract_digest(
        tmp_path, {"id": "other-model", "options": []}
    )
    assert baseline != contract.scenario_verifier_contract_digest(
        tmp_path, {"id": "composer-2.5", "options": ["different-policy"]}
    )
    with pytest.raises(ValueError, match="SCENARIO_VERIFIER_MODEL_UNBOUND"):
        contract.scenario_verifier_contract_digest(tmp_path, {"options": []})


def test_missing_shared_policy_file_fails_closed(tmp_path, monkeypatch):
    _policy_workspace(tmp_path, monkeypatch)
    (tmp_path / _SHARED_VALIDATION[0]).unlink()
    with pytest.raises(ValueError, match="SCENARIO_VERIFIER_CONTRACT_MISSING"):
        contract.scenario_verifier_contract_digest(tmp_path, _MODEL)


@pytest.mark.parametrize("changed_file", _SHARED_VALIDATION)
def test_shared_validation_change_invalidates_all_scenario_row_digests(
    tmp_path, monkeypatch, changed_file
):
    baseline = _policy_workspace(tmp_path, monkeypatch)
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    path_a = test_dir / "test_a.py"
    path_b = test_dir / "test_b.py"
    path_a.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    path_b.write_text("def test_b():\n    assert True\n", encoding="utf-8")
    mappings = {
        "a": "tests/test_a.py::test_a",
        "b": "tests/test_b.py::test_b",
    }
    plan = tmp_path / "openspec/changes/demo/scenario-coverage/plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("- SCENARIO_ID: a\n- SCENARIO_ID: b\n", encoding="utf-8")
    def scope(policy_digest):
        return impact_scope(
            tmp_path, {path_a, path_b, plan}, mappings,
            authority={
                "model": _MODEL,
                "plan_evidence_id": "frozen-plan",
                "verifier_contract_digest": policy_digest,
            },
        )
    before = scope(baseline)
    target = tmp_path / changed_file
    target.write_bytes(target.read_bytes() + b"\n# changed validation authority\n")
    after = scope(contract.scenario_verifier_contract_digest(tmp_path, _MODEL))
    assert after["shared_digest"] == before["shared_digest"]
    assert after["row_digests"] == before["row_digests"]


@pytest.mark.parametrize("changed_file", _TRANSPORT_ONLY)
def test_transport_and_receipt_code_do_not_invalidate_review_policy(
    tmp_path, monkeypatch, changed_file,
):
    baseline = _policy_workspace(tmp_path, monkeypatch)
    path = tmp_path / changed_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# independent implementation change\n", encoding="utf-8")
    assert contract.scenario_verifier_contract_digest(tmp_path, _MODEL) == baseline
