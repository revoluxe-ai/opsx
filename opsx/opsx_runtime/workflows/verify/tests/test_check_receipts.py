"""Cheap PASS reuse: independent of orchestration, HEAD, SDK and model tier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.check_receipts import (
    SCENARIO_COVERAGE_CONTRACT,
    best_effort_write_pass,
    content_digest,
    read_pass,
    write_pass,
)

CHECKS = (
    "scenario_coverage", "impl_verify", "quality_gates", "gate_repair",
    "coverage_activation", "project_bug", "bugbot", "security",
)


def _repo(root: Path) -> Path:
    folder = root / "openspec/changes/demo"
    folder.mkdir(parents=True)
    return root


@pytest.mark.parametrize("check", CHECKS)
def test_one_common_receipt_format_for_every_check(tmp_path: Path, check: str):
    root = _repo(tmp_path)
    file = root / "src/stroy/example.py"
    file.parent.mkdir(parents=True)
    file.write_text("value = 1\n", encoding="utf-8")
    digest = content_digest(root, [file])
    write_pass(
        root, change_id="demo", check=check, pass_verdict="PASS", contract="check-contract-v1",
        input_digest=digest, origin_execution_id="exec-a",
        evidence_id="sdk-run-a:agent-a", result={"verdict": "PASS", "rows": [1]},
    )
    receipt = read_pass(
        root, change_id="demo", check=check,
        pass_verdict="PASS", contract="check-contract-v1", input_digest=digest,
    )
    assert receipt is not None
    assert receipt["origin_execution_id"] == "exec-a"
    assert receipt["status"] == "PASS"
    assert receipt["result"]["rows"] == [1]

    # Reuse is independent of Git HEAD, checkpoint and requested model.
    file.write_text("value = 2\n", encoding="utf-8")
    assert read_pass(
        root, change_id="demo", check=check, pass_verdict="PASS", contract="check-contract-v1",
        input_digest=content_digest(root, [file]),
    ) is None




def test_split_product_and_verifier_identities_are_required_for_v2(tmp_path: Path):
    root = _repo(tmp_path)
    common = dict(
        root=root, change_id="demo", check="impl_semantic_review",
        contract="read_only_review_v2", input_digest="product-a",
        pass_verdict="IMPL_OK",
    )
    with pytest.raises(ValueError, match="CHECK_VERIFIER_CONTRACT_UNBOUND"):
        write_pass(
            **common, origin_execution_id="exec-a", evidence_id="run:agent",
            result={"verdict": "IMPL_OK"},
        )
    write_pass(
        **common, verifier_contract_digest="verifier-v1",
        origin_execution_id="exec-a", evidence_id="run:agent",
        result={"verdict": "IMPL_OK"},
    )
    current = read_pass(**common, verifier_contract_digest="verifier-v1")
    assert current is not None
    assert current["product_input_digest"] == "product-a"
    assert current["verifier_contract_digest"] == "verifier-v1"
    assert read_pass(**common, verifier_contract_digest="verifier-v2") is None
    assert read_pass(**{**common, "input_digest": "product-b"},
                     verifier_contract_digest="verifier-v1") is None
    assert read_pass(**common) is None

    # A legacy receipt without a verifier identity cannot satisfy v2.
    legacy = {**common, "contract": "read_only_review_v1"}
    write_pass(
        **legacy, origin_execution_id="exec-b", evidence_id="old:agent",
        result={"verdict": "IMPL_OK"},
    )
    assert read_pass(**common, verifier_contract_digest="verifier-v1") is None


def test_unrelated_file_or_receipt_edit_does_not_invalidate(tmp_path: Path):
    root = _repo(tmp_path)
    target = root / "tests/test_foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_foo(): assert True\n", encoding="utf-8")
    original = content_digest(root, [target])
    unrelated = root / "logs/run.log"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("new log\n", encoding="utf-8")
    assert content_digest(root, [target]) == original


def test_added_relevant_file_changes_the_digest(tmp_path: Path):
    root = _repo(tmp_path)
    directory = root / "src/stroy"
    directory.mkdir(parents=True)
    a = directory / "a.py"
    a.write_text("x=1\n", encoding="utf-8")
    before = content_digest(root, directory.rglob("*.py"))
    (directory / "b.py").write_text("x=2\n", encoding="utf-8")
    assert content_digest(root, directory.rglob("*.py")) != before


def test_corruption_or_contract_version_change_is_miss(tmp_path: Path):
    root = _repo(tmp_path)
    write_pass(
        root, change_id="demo", check="scenario_coverage",
        pass_verdict="PASS", contract=SCENARIO_COVERAGE_CONTRACT, input_digest="digest-a",
        origin_execution_id="exec-a", evidence_id="sdk-run:agent",
        result={"verdict": "PASS"},
    )
    getter = lambda **kwargs: read_pass(
        root, change_id="demo", check="scenario_coverage",
        pass_verdict="PASS", contract=kwargs.get("contract", SCENARIO_COVERAGE_CONTRACT),
        input_digest=kwargs.get("input_digest", "digest-a"),
    )
    assert getter() is not None
    assert getter(contract="changed") is None
    assert getter(input_digest="changed") is None

    path = root / "opsx/receipts/demo/check-receipts/scenario_coverage.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["result"] = {"verdict": "FORGED_PASS"}
    path.write_text(json.dumps(obj), encoding="utf-8")
    assert getter() is None
    path.write_text("{bad json", encoding="utf-8")
    assert getter() is None


def test_invalid_unbound_receipt_is_never_published(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="UNBOUND"):
        write_pass(
            root, change_id="demo", check="security", pass_verdict="PASS", contract="v1",
            input_digest="x", origin_execution_id="", evidence_id="",
            result={"verdict": "PASS"},
        )
    assert not (root / "opsx/receipts/demo/check-receipts/security.json").exists()


def test_bad_path_and_symlink_do_not_escape_repo(tmp_path: Path):
    root = _repo(tmp_path)
    for change_id in ("..", "../escape", "/abs/path"):
        assert read_pass(
            root, change_id=change_id, check="scenario_coverage",
            pass_verdict="PASS", contract="v1", input_digest="d",
        ) is None
    with pytest.raises(ValueError, match="CHECK_CHANGE_INVALID"):
        write_pass(
            root, change_id="../escape", check="scenario_coverage",
            pass_verdict="PASS", contract="v1", input_digest="x", origin_execution_id="exec",
            evidence_id="run", result={"verdict":"PASS"},
        )
    safe = root / "tests/test.py"
    safe.parent.mkdir(parents=True)
    safe.write_text("x=1\n", encoding="utf-8")
    alias = root / "tests/link.py"
    try:
        alias.symlink_to(safe)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(ValueError, match="LINK"):
        content_digest(root, [alias])


def test_receipt_write_failure_is_nonfatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(
        "agents.check_receipts.write_pass",
        lambda **kwargs: (_ for _ in ()).throw(OSError("read only")),
    )
    assert best_effort_write_pass(
        root=root, change_id="demo", check="scenario_coverage",
        pass_verdict="PASS", contract="v1", input_digest="digest", origin_execution_id="exec",
        evidence_id="run", result={"verdict": "PASS"},
    ) is False


def test_input_fingerprint_uses_contents_not_mtime(tmp_path: Path):
    root = _repo(tmp_path)
    src = root / "src/stroy/example.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n", encoding="utf-8")
    first = content_digest(root, [src])
    src.touch()
    assert content_digest(root, [src]) == first



@pytest.mark.asyncio
async def test_legacy_full_cycle_bit_never_skips_independent_checks(monkeypatch):
    import workflows.verify.cycle_checks as checks
    import workflows.verify.cycle_gate as gate

    monkeypatch.setattr(checks, "resolve_code_fingerprint", lambda state: "fingerprint")
    state = {
        "cycle_checks": {"established": {"full_verify_cycle": "fingerprint"}},
        "verify_cycle_skipped": True,
        "run_uuid": "execution-new",
    }
    patch = await gate.verify_cycle_gate_node(state)
    assert patch["verify_cycle_skipped"] is False
    assert gate.route_after_verify_cycle_gate({**state, **patch}) == "impl_verify"

    # Even a historical green state with no pending work must not bypass
    # fresh source/model/environment evidence checks.
    state.pop("verify_cycle_skipped")
    patch = await gate.verify_cycle_gate_node(state)
    assert patch.get("verify_cycle_skipped") is not True
    assert gate.route_after_verify_cycle_gate({**state, **patch}) == "impl_verify"




def test_writer_rejects_negative_verdict_even_if_caller_requests_pass(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="VERDICT_MISMATCH"):
        write_pass(
            root, change_id="demo", check="scenario_coverage",
            contract=SCENARIO_COVERAGE_CONTRACT,
            pass_verdict="SCENARIO_TEST_COVERAGE_PASS",
            input_digest="sha-abc", origin_execution_id="exec-a",
            evidence_id="run-a:agent-a",
            result={"verdict": "SCENARIO_TEST_COVERAGE_FAIL"},
        )
    assert not (
        root / "opsx/receipts/demo/check-receipts/scenario_coverage.json"
    ).exists()
