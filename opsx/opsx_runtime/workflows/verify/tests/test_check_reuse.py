"""Durable read-only evidence: never reuse stale, downgraded, or incomplete checks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from agents.verify.check_reuse import (
    _PRODUCT_DIRS_BY_ROLE, _PRODUCT_ROOT_FILES_BY_ROLE,
    _VERIFIER_POLICY_DIR_BY_ROLE, _receipt_check_name, _source_files,
    _verifier_contract_files,
    read_clean_review, review_receipt_key, reused_review_transport_patch,
    save_clean_review,
)


class CleanAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    findings: list[str]


def _repo(root: Path) -> Path:
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "design.md").write_text("normative design\n", encoding="utf-8")
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "example.py").write_text("behavior = 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("assert True\n", encoding="utf-8")
    # Hermetic test repositories also carry per-role verifier policy files.
    # The real runtime requires verifier authority rather than a silent default.
    for scope in (
        "impl_verify", "project_bug_hunt", "bugbot",
        "security_review", "coverage_activation",
    ):
        policy = root / "opsx/opsx_runtime/agents/verify" / scope / "agent.py"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("contract = 1\n", encoding="utf-8")
    return root


def _commit_stage(root: Path) -> None:
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "config", "user.name", "CI"],
        ["git", "add", "."],
        ["git", "commit", "-m", "baseline"],
        ["git", "branch", "stage"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)


def _state(root: Path, *, run: str = "exec-1", model: str = "composer-2.5") -> dict:
    return {
        "repository_path": str(root),
        "change_id": "demo",
        "run_uuid": run,
        "model": {"id": model, "options": []},
    }


def _save(state: dict, role: str, level: int | None = None) -> None:
    key = review_receipt_key(state, role=role, level=level, prompt="stable audit prompt")
    assert key is not None
    assert save_clean_review(
        key, answer=CleanAnswer(status="CLEAN", findings=[]),
        pass_verdict="CLEAN", clean=True,
        outcome=SimpleNamespace(status="finished", run_id="run-a", agent_id="agent-a"),
    )


def test_impl_reuse_across_executions_and_model_changes_but_not_source_changes(tmp_path):
    root = _repo(tmp_path)
    state = _state(root)
    _save(state, "impl_verify")
    key = review_receipt_key(
        _state(root, run="exec-2"), role="impl_verify", prompt="stable audit prompt",
    )
    assert read_clean_review(
        key, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN" and not x.findings,
    ) is not None
    assert key is not None
    unrelated = root / "opsx/receipts/demo/run.log"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("new logs\n", encoding="utf-8")
    assert review_receipt_key(
        _state(root, run="exec-2"), role="impl_verify", prompt="stable audit prompt",
    ).digest == key.digest
    (root / "src/stroy/example.py").write_text("behavior = 2\n", encoding="utf-8")
    assert review_receipt_key(
        _state(root, run="exec-2"), role="impl_verify", prompt="stable audit prompt",
    ).digest != key.digest
    (root / "src/stroy/example.py").write_text("behavior = 1\n", encoding="utf-8")
    changed_model = review_receipt_key(
        _state(root, model="new-tier"), role="impl_verify", prompt="stable audit prompt",
    )
    assert changed_model is not None
    assert changed_model.digest == key.digest
    assert changed_model.verifier_contract_digest is key.verifier_contract_digest is None


@pytest.mark.parametrize("change", ["edit", "add", "remove", "rename"])
def test_impl_decisions_content_and_membership_invalidate_receipt(tmp_path, change):
    root = _repo(tmp_path)
    decisions = root / "openspec/changes/demo/decisions"
    original = decisions / "architecture" / "decision.md"
    original.parent.mkdir(parents=True)
    original.write_text("approved boundary: outbox only\n", encoding="utf-8")
    _save(_state(root), "impl_verify")

    before = review_receipt_key(
        _state(root, run="exec-2"), role="impl_verify", prompt="stable audit prompt",
    )
    assert before is not None
    assert read_clean_review(
        before, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN" and not x.findings,
    ) is not None

    if change == "edit":
        original.write_text("approved boundary: direct outbound\n", encoding="utf-8")
    elif change == "add":
        added = decisions / "additional" / "decision.md"
        added.parent.mkdir(parents=True)
        added.write_text("approved channel: max\n", encoding="utf-8")
    elif change == "remove":
        original.unlink()
    elif change == "rename":
        original.rename(original.with_name("renamed-decision.md"))

    after = review_receipt_key(
        _state(root, run="exec-3"), role="impl_verify", prompt="stable audit prompt",
    )
    assert after is not None
    assert after.digest != before.digest
    assert read_clean_review(
        after, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN" and not x.findings,
    ) is None




def test_impl_receipt_reuse_ignores_opsx_runtime_and_all_tests(tmp_path):
    root = _repo(tmp_path)
    _save(_state(root), "impl_verify")
    prior = review_receipt_key(
        _state(root), role="impl_verify", prompt="stable audit prompt",
    )
    assert prior is not None

    # Scenario queue/concurrency, checkpoint resume, SDK transport, OPSX tests,
    # and even product tests belong to other verification responsibilities.
    unrelated = (
        "opsx/opsx_runtime/workflows/verify/cycle_gate.py",
        "opsx/opsx_runtime/workflows/verify/tests/test_check_reuse.py",
        "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_single.py",
        "opsx/opsx_runtime/agents/adapters/provider_stream.py",
        "tests/opsx/test_opsx_harness.py",
        "tests/test_example.py",
        "pytest.ini",
    )
    for rel in unrelated:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("new test/OPSX infrastructure behavior\\n", encoding="utf-8")
        after = review_receipt_key(
            _state(root, run="exec-next"), role="impl_verify",
            prompt="stable audit prompt",
        )
        assert after is not None and after.digest == prior.digest, rel
        assert read_clean_review(
            after, model=CleanAnswer, pass_verdict="CLEAN",
            clean=lambda x: x.status == "CLEAN" and not x.findings,
        ) is not None, rel


@pytest.mark.parametrize("rel", (
    "opsx/opsx_runtime/agents/verify/impl_verify/agent.py",
    "opsx/opsx_runtime/agents/verify/impl_verify/preflight.py",
    "opsx/opsx_runtime/agents/verify/impl_verify/prompt/get_prompt.py",
    "opsx/opsx_runtime/agents/verify/impl_verify/schemas.py",
    "opsx/opsx_runtime/agents/response_schema.py",
    "pyproject.toml",
    "poetry.lock",
))
def test_impl_ignores_reviewer_changes_but_tracks_product_dependencies(tmp_path, rel):
    root = _repo(tmp_path)
    policy = root / rel
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("initial authoritative input\\n", encoding="utf-8")
    _save(_state(root), "impl_verify")
    before = review_receipt_key(
        _state(root), role="impl_verify", prompt="stable audit prompt",
    )
    assert before is not None

    policy.write_text("changed authoritative input\\n", encoding="utf-8")
    after = review_receipt_key(
        _state(root, run="exec-next"), role="impl_verify",
        prompt="stable audit prompt",
    )
    assert after is not None
    if rel.startswith("opsx/"):
        assert after.digest == before.digest
        assert after.verifier_contract_digest is before.verifier_contract_digest is None
    else:
        assert after.digest != before.digest
    reused = read_clean_review(
        after, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN" and not x.findings,
    )
    assert (reused is not None) == rel.startswith("opsx/")


@pytest.mark.parametrize("role", ("project_bug", "bugbot", "security"))
def test_opsx_tests_do_not_invalidate_other_review_roles(tmp_path, role):
    root = _repo(tmp_path)
    suite = root / "tests/opsx/test_opsx_harness.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("initial harness\\n", encoding="utf-8")
    runtime_test = root / "opsx/opsx_runtime/workflows/verify/tests/test_smoke.py"
    runtime_test.parent.mkdir(parents=True)
    runtime_test.write_text("initial runtime test\\n", encoding="utf-8")
    _commit_stage(root)
    level = None if role == "project_bug" else 1
    prior = review_receipt_key(
        _state(root), role=role, level=level, prompt="stable audit prompt",
    )
    assert prior is not None
    suite.write_text("changed harness\\n", encoding="utf-8")
    runtime_test.write_text("changed runtime test\\n", encoding="utf-8")
    after = review_receipt_key(
        _state(root, run="exec-next"), role=role, level=level,
        prompt="stable audit prompt",
    )
    assert after is not None and after.digest == prior.digest


@pytest.mark.parametrize("role", ("project_bug", "bugbot", "security"))
def test_product_tests_invalidate_code_review_roles(tmp_path, role):
    root = _repo(tmp_path)
    _commit_stage(root)
    level = None if role == "project_bug" else 1
    prior = review_receipt_key(
        _state(root), role=role, level=level, prompt="stable audit prompt",
    )
    assert prior is not None
    (root / "tests/test_example.py").write_text("assert False\\n", encoding="utf-8")
    after = review_receipt_key(
        _state(root, run="exec-next"), role=role, level=level,
        prompt="stable audit prompt",
    )
    assert after is not None and after.digest != prior.digest




def test_receipt_check_names_are_per_role_not_per_level():
    assert _receipt_check_name("project_bug", None) == "project_bug"
    assert _receipt_check_name("bugbot", 1) == "bugbot"
    assert _receipt_check_name("bugbot", 2) == "bugbot"
    assert _receipt_check_name("security", 3) == "security"
    assert _receipt_check_name("coverage_activation", 1) == "coverage_activation_level_1"


def test_per_check_invalidation_directories_are_explicit():
    roles = (
        "impl_verify", "archive_verify", "project_bug", "bugbot",
        "security", "coverage_activation",
    )
    assert set(_PRODUCT_DIRS_BY_ROLE) == set(roles)
    assert set(_PRODUCT_ROOT_FILES_BY_ROLE) == set(roles)
    assert set(_VERIFIER_POLICY_DIR_BY_ROLE) == set(roles) - {"impl_verify"}
    assert _PRODUCT_DIRS_BY_ROLE["impl_verify"] == ("src", "migrations")
    assert _PRODUCT_DIRS_BY_ROLE["project_bug"] == ("src", "tests")
    assert _PRODUCT_DIRS_BY_ROLE["bugbot"] == ("src", "tests")
    assert _PRODUCT_DIRS_BY_ROLE["security"] == ("src", "tests")
    assert _PRODUCT_DIRS_BY_ROLE["coverage_activation"] == ("src", "migrations", "tests")
    assert _PRODUCT_ROOT_FILES_BY_ROLE["project_bug"] == ()
    assert _PRODUCT_ROOT_FILES_BY_ROLE["bugbot"] == ()
    assert _PRODUCT_ROOT_FILES_BY_ROLE["security"] == ()
    expected_verifiers = {
        "archive_verify": "archive_verify",
        "project_bug": "project_bug_hunt",
        "bugbot": "bugbot",
        "security": "security_review",
        "coverage_activation": "coverage_activation",
    }
    for role, scope in expected_verifiers.items():
        assert _VERIFIER_POLICY_DIR_BY_ROLE[role] == (
            f"opsx/opsx_runtime/agents/verify/{scope}",
        )


@pytest.mark.parametrize("role", (
    "impl_verify", "project_bug", "bugbot", "security", "coverage_activation",
))
def test_per_check_product_files_never_include_opsx(tmp_path, role):
    root = _repo(tmp_path)
    opsx = root / "opsx/opsx_runtime/agents/verify/impl_verify/agent.py"
    opsx.parent.mkdir(parents=True, exist_ok=True)
    opsx.write_text("policy = 1\\n", encoding="utf-8")
    product = _source_files(root, "demo", role)
    assert all("opsx" not in path.relative_to(root).parts for path in product)
    assert root / "src/stroy/example.py" in product
    tests_in_product = root / "tests/test_example.py" in product
    if role in ("project_bug", "bugbot", "security", "coverage_activation"):
        assert tests_in_product
    elif role == "impl_verify":
        assert not tests_in_product
    else:
        assert tests_in_product
    if role == "impl_verify":
        return  # This check no longer has a verifier contract.
    verifier = _verifier_contract_files(root, role)
    assert opsx not in verifier
    assert all(
        "tests" not in path.relative_to(root).parts
        for path in verifier
    )


@pytest.mark.parametrize("role", (
    "impl_verify", "project_bug", "bugbot", "security", "coverage_activation",
))
def test_role_verifier_change_only_invalidates_its_own_contract(tmp_path, role):
    root = _repo(tmp_path)
    if role == "impl_verify":
        scope = "opsx/opsx_runtime/agents/verify/impl_verify"
    else:
        scope = _VERIFIER_POLICY_DIR_BY_ROLE[role][0]
    own_policy = root / scope / "agent.py"
    own_policy.parent.mkdir(parents=True, exist_ok=True)
    own_policy.write_text("rule = 1\\n", encoding="utf-8")
    baseline = review_receipt_key(
        _state(root), role=role, prompt="stable audit prompt",
    )
    if role in ("project_bug", "bugbot", "security"):
        _commit_stage(root)
        baseline = review_receipt_key(
            _state(root), role=role, prompt="stable audit prompt",
        )
    assert baseline is not None
    own_policy.write_text("rule = 2\\n", encoding="utf-8")
    changed = review_receipt_key(
        _state(root, run="exec-2"), role=role, prompt="stable audit prompt",
    )
    assert changed is not None
    assert changed.digest == baseline.digest
    if role == "impl_verify":
        assert changed.verifier_contract_digest is baseline.verifier_contract_digest is None
    else:
        assert changed.verifier_contract_digest != baseline.verifier_contract_digest


@pytest.mark.parametrize("role", (
    "impl_verify", "project_bug", "bugbot", "security", "coverage_activation",
))
def test_product_code_change_invalidates_each_role_product_input(tmp_path, role):
    root = _repo(tmp_path)
    if role in ("project_bug", "bugbot", "security"):
        _commit_stage(root)
    before = review_receipt_key(_state(root), role=role, prompt="prompt")
    assert before is not None
    (root / "src/stroy/example.py").write_text("behavior = 2\\n", encoding="utf-8")
    after = review_receipt_key(_state(root, run="exec-2"), role=role, prompt="prompt")
    assert after is not None
    assert after.digest != before.digest
    assert after.verifier_contract_digest == before.verifier_contract_digest


def test_impl_unrelated_empty_commit_does_not_invalidate_receipt(tmp_path):
    root = _repo(tmp_path)
    decisions = root / "openspec/changes/demo/decisions/architecture"
    decisions.mkdir(parents=True)
    (decisions / "decision.md").write_text("keep source contract\n", encoding="utf-8")
    _commit_stage(root)
    before = review_receipt_key(
        _state(root), role="impl_verify", prompt="stable audit prompt",
    )
    assert before is not None
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "unrelated metadata"],
        cwd=root, check=True, capture_output=True,
    )
    after = review_receipt_key(
        _state(root, run="exec-2"), role="impl_verify", prompt="stable audit prompt",
    )
    assert after is not None
    assert after.digest == before.digest




@pytest.mark.parametrize("role", ("project_bug", "bugbot", "security"))
def test_diff_review_key_does_not_depend_on_git_head_or_opsx_commit(tmp_path, role):
    root = _repo(tmp_path)
    _commit_stage(root)
    level = None if role == "project_bug" else 1
    original = review_receipt_key(
        _state(root), role=role, level=level, prompt="stable review",
    )
    assert original is not None

    # New commit and branch identity without any reviewed product change.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "metadata-only"],
        cwd=root, check=True, capture_output=True,
    )
    opsx = root / "opsx/opsx_runtime/workflows/verify/cycle_gate.py"
    opsx.parent.mkdir(parents=True, exist_ok=True)
    opsx.write_text("updated scheduling\\n", encoding="utf-8")
    subprocess.run(["git", "add", str(opsx.relative_to(root))],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "OPSX-only update"],
                   cwd=root, check=True, capture_output=True)
    unchanged = review_receipt_key(
        _state(root, run="exec-2"), role=role, level=level, prompt="stable review",
    )
    assert unchanged is not None
    assert unchanged.digest == original.digest
    assert unchanged.verifier_contract_digest == original.verifier_contract_digest

    (root / "src/stroy/example.py").write_text("behavior = 2\\n", encoding="utf-8")
    fresh = review_receipt_key(
        _state(root, run="exec-3"), role=role, level=level, prompt="stable review",
    )
    assert fresh is not None and fresh.digest != original.digest


def test_impl_decision_symlink_must_fail_closed(tmp_path):
    root = _repo(tmp_path)
    decision_dir = root / "openspec/changes/demo/decisions"
    outside = root / "another-location"
    outside.mkdir()
    try:
        decision_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported by this runner")
    assert review_receipt_key(
        _state(root), role="impl_verify", prompt="stable audit prompt",
    ) is None


@pytest.mark.parametrize("role", ["project_bug", "bugbot", "security"])
def test_diff_review_reuse_requires_baseline_and_exact_level(tmp_path, role):
    root = _repo(tmp_path)
    level = 1 if role != "project_bug" else None
    # Без origin/stage — sentinel baseline; ключ строится локально по product tree.
    unbound = review_receipt_key(
        _state(root), role=role, level=level, prompt="stable audit prompt",
    )
    assert unbound is not None
    _commit_stage(root)
    state = _state(root)
    _save(state, role, level)
    key = review_receipt_key(
        state, role=role, level=level, prompt="stable audit prompt",
    )
    assert key is not None
    assert read_clean_review(
        key, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN" and not x.findings,
    )
    if level:
        alt = review_receipt_key(
            state, role=role, level=2, prompt="stable audit prompt",
        )
        assert alt is not None
        assert alt.check == role
        assert key.check == role
    # Diff pack / prompt text не входит в durable identity — только product + policy.
    same = review_receipt_key(
        state, role=role, level=level, prompt="different instructions",
    )
    assert same is not None
    assert same.verifier_contract_digest == key.verifier_contract_digest
    assert same.digest == key.digest


@pytest.mark.parametrize("role", ["project_bug", "bugbot", "security"])
def test_diff_review_rejects_enclosing_checkout_baseline(tmp_path, role):
    """Git parent discovery must not substitute another repository's stage."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "README.md").write_text("outer checkout\n", encoding="utf-8")
    _commit_stage(outer)
    nested = _repo(outer / "nested")

    level = None if role == "project_bug" else 1
    state = _state(nested)
    assert review_receipt_key(
        state, role=role, level=level, prompt="stable audit prompt",
    ) is None

    # Independent nested checkout with its own stage is a valid baseline.
    _commit_stage(nested)
    key = review_receipt_key(
        state, role=role, level=level, prompt="stable audit prompt",
    )
    assert key is not None


@pytest.mark.parametrize("role", ("project_bug", "bugbot", "security"))
def test_product_review_ignores_opsx_receipts_and_dynamic_artifacts(tmp_path, role):
    root = _repo(tmp_path)
    _commit_stage(root)
    level = None if role == "project_bug" else 1
    before = review_receipt_key(
        _state(root), role=role, level=level, prompt="stable audit prompt",
    )
    assert before is not None
    verify_log = root / "opsx/receipts/demo/run/live.ndjson"
    verify_log.parent.mkdir(parents=True)
    verify_log.write_text('{"line": 1}\n', encoding="utf-8")
    contract = root / "openspec/changes/demo/owner-contract.md"
    contract.write_text("dynamic\n", encoding="utf-8")
    after = review_receipt_key(
        _state(root, run="exec-2"), role=role, level=level, prompt="stable audit prompt",
    )
    assert after is not None
    assert after.digest == before.digest
    (root / "openspec/changes/demo/design.md").write_text("revised static plan\n", encoding="utf-8")
    fresh = review_receipt_key(
        _state(root, run="exec-3"), role=role, level=level, prompt="stable audit prompt",
    )
    assert fresh is not None
    assert fresh.digest != before.digest


def test_negative_or_unfinished_review_never_creates_receipt(tmp_path):
    root = _repo(tmp_path)
    state = _state(root)
    key = review_receipt_key(state, role="impl_verify", prompt="audit")
    answer = CleanAnswer(status="FINDINGS", findings=["bug"])
    assert not save_clean_review(
        key, answer=answer, pass_verdict="CLEAN", clean=False,
        outcome=SimpleNamespace(status="finished", run_id="r", agent_id="a"),
    )
    assert not save_clean_review(
        key, answer=CleanAnswer(status="CLEAN", findings=[]),
        pass_verdict="CLEAN", clean=True,
        outcome=SimpleNamespace(status="running", run_id="r", agent_id="a"),
    )
    assert read_clean_review(
        key, model=CleanAnswer, pass_verdict="CLEAN",
        clean=lambda x: x.status == "CLEAN",
    ) is None


def test_previous_sdk_identity_is_never_restored():
    patch = reused_review_transport_patch()
    assert patch["agent_id"] == ""
    assert patch["run_id"] is None
    assert patch["execution_status"] == "REUSED_PASS"
    assert patch["validation_attempts"] == 0
    assert patch["usage"] is None
    assert patch["cost"] is None

