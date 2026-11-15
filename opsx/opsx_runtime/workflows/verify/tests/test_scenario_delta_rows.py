"""Contract tests: reuse only exact supported row evidence; all ambiguity is a MISS."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.common.scenario_files import scenario_test_relpath
from agents.verify.scenario_coverage_verify.delta_rows import (
    assemble_rows,
    impact_scope,
    ledger_from_validated_rows,
    select_reusable_rows,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from agents.verify.scenario_coverage_verify.evidence_contract import source_paths


def _demo(root: Path):
    base = root / "openspec/changes/demo/scenario-coverage"
    base.mkdir(parents=True)
    ensure_machine_receipt_dirs(base)
    (base / "plan.md").write_text(
        "- SCENARIO_ID: a\n- SCENARIO_ID: b\n", encoding="utf-8",
    )
    plan_receipt_path(base).write_text('{"id": 1}\n', encoding="utf-8")
    trace_matrix_path(base).write_text(
        '{"entries":[{"scenario_id":"a","test_node_id":"tests/test_a.py::test_a"},'
        '{"scenario_id":"b","test_node_id":"tests/test_b.py::test_b"}]}',
        encoding="utf-8",
    )
    specs = root / "openspec/changes/demo/specs"
    specs.mkdir()
    (specs / "spec.md").write_text("WHEN A THEN B\n", encoding="utf-8")
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "app.py").write_text("def f(): return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (tests / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    (tests / "test_b.py").write_text("def test_b():\n    assert 2\n", encoding="utf-8")
    mapping = {"a": "tests/test_a.py::test_a", "b": "tests/test_b.py::test_b"}
    authority = {
        "model": {"id": "composer-2.5", "options": []},
        "plan_evidence_id": "approved-id",
        "verifier_contract_digest": "review-v1",
    }
    return root, mapping, authority


def _paths(root):
    # Exercise the same explicit inventory used in production; globbing the
    # entire tmp checkout would accidentally hash .git metadata after init.
    return source_paths(root, "demo")


def _origin():
    return {"origin_execution_id": "exec-1", "evidence_id": "run-a:agent-a"}


def _baseline(root, mapping, authority, *, supported=("a", "b")):
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    rows = [
        {
            "scenario_id": sid, "test_node_id": node,
            "assessment": "SUPPORTED" if sid in supported else "GAP",
            "evidence": "actual source inspection",
            "when_proofs": [1] if sid in supported else [],
            "outcome_proofs": [2] if sid in supported else [],
        }
        for sid, node in mapping.items()
    ]
    ledger = ledger_from_validated_rows(
        plan_ids=list(mapping), current_scope=scope, row_reviews=rows,
        provenance={sid: _origin() for sid in supported},
    )
    return scope, ledger


def _selected(root, mapping, authority, ledger):
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    return scope, *select_reusable_rows(
        plan_ids=list(mapping), mapping=mapping,
        current_scope=scope, prior_ledger=ledger,
    )


def test_unchanged_rows_reuse_with_original_sdk_identity(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    _, cached, pending, origins = _selected(root, mapping, authority, ledger)
    assert list(cached) == ["a", "b"]
    assert not pending
    assert origins["a"] == _origin()


def test_initial_fail_caches_only_supported_row(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority, supported=("b",))
    _, cached, pending, origins = _selected(root, mapping, authority, ledger)
    assert list(cached) == ["b"]
    assert pending == ["a"]
    assert "a" not in origins


def test_canonical_scenario_test_edit_invalidates_only_mapped_row(tmp_path):
    root = tmp_path
    base = root / "openspec/changes/demo/scenario-coverage"
    base.mkdir(parents=True)
    ensure_machine_receipt_dirs(base)
    (base / "plan.md").write_text("- SCENARIO_ID: a\n- SCENARIO_ID: b\n", encoding="utf-8")
    plan_receipt_path(base).write_text('{"id": 1}\n', encoding="utf-8")
    change = "demo"
    sid_a, sid_b = "a", "b"
    rel_a = scenario_test_relpath(change, sid_a)
    rel_b = scenario_test_relpath(change, sid_b)
    for rel, sid, body in (
        (rel_a, sid_a, "def test_a():\n    assert 1\n"),
        (rel_b, sid_b, "def test_b():\n    assert 2\n"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    trace_matrix_path(base).write_text(
        json.dumps(
            {
                "entries": [
                    {"scenario_id": sid_a, "test_node_id": f"{rel_a}::test_a"},
                    {"scenario_id": sid_b, "test_node_id": f"{rel_b}::test_b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "openspec/changes/demo/specs").mkdir(parents=True)
    (root / "openspec/changes/demo/specs/spec.md").write_text("spec\n", encoding="utf-8")
    mapping = {sid_a: f"{rel_a}::test_a", sid_b: f"{rel_b}::test_b"}
    authority = {
        "model": {"id": "composer-2.5", "options": []},
        "plan_evidence_id": "approved-id",
        "verifier_contract_digest": "review-v1",
    }
    scope, ledger = _baseline(root, mapping, authority)
    (root / rel_a).write_text("def test_a():\n    assert 3\n", encoding="utf-8")
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert list(cached) == [sid_b] and pending == [sid_a]


def test_leaf_test_edit_invalidates_only_mapped_row(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    scope, ledger = _baseline(root, mapping, authority)
    (root / "tests/test_a.py").write_text("def test_a():\n    assert 3\n", encoding="utf-8")
    next_scope, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert next_scope["shared_digest"] == scope["shared_digest"]
    assert list(cached) == ["b"] and pending == ["a"]


def test_two_scenarios_sharing_test_module_both_invalidated(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    mapping["c"] = "tests/test_a.py::test_alternate"
    plan = root / "openspec/changes/demo/scenario-coverage/plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + "- SCENARIO_ID: c\n", encoding="utf-8")
    _, ledger = _baseline(root, mapping, authority)
    (root / "tests/test_a.py").write_text("def test_a():\n    assert 4\n", encoding="utf-8")
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert list(cached) == ["b"]
    assert pending == ["a", "c"]


@pytest.mark.parametrize("path,updated", [
    ("src/stroy/app.py", "def f(): return 999\n"),
    ("tests/conftest.py", "broken Python that is not a mapped test\n"),
    ("opsx/receipts/demo/scenario-coverage/plan.receipt.json", '{"id": 99}\n'),
])
def test_unrelated_files_do_not_invalidate_scenario_review(tmp_path, path, updated):
    root, mapping, authority = _demo(tmp_path)
    before, ledger = _baseline(root, mapping, authority)
    (root / path).write_text(updated, encoding="utf-8")
    after, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert after == before
    assert set(reused) == {"a", "b"} and not pending


def test_changed_plan_entry_does_not_invalidate_product_receipts(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    plan = root / "openspec/changes/demo/scenario-coverage/plan.md"
    plan.write_text(
        "- SCENARIO_ID: a\n  REQUIRED_EVIDENCE: stronger\n"
        "- SCENARIO_ID: b\n", encoding="utf-8",
    )
    _, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert set(reused) == {"a", "b"} and not pending


def test_mapping_reformat_does_not_invalidate_unchanged_scenarios(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    matrix = trace_matrix_path(root / "openspec/changes/demo/scenario-coverage")
    matrix.write_text(json.dumps({
        "entries": [
            {"scenario_id": sid, "test_node_id": node}
            for sid, node in mapping.items()
        ],
    }, indent=2), encoding="utf-8")
    _, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert set(reused) == {"a", "b"} and not pending


@pytest.mark.parametrize("mutation", ["add", "rename", "remove"])
def test_test_module_membership_change_invalidates_only_relevant_inputs(tmp_path, mutation):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    a = root / "tests/test_a.py"
    if mutation == "add":
        (root / "tests/test_new.py").write_text("def test_new(): pass\n", encoding="utf-8")
    elif mutation == "rename":
        a.rename(root / "tests/test_renamed.py")
    else:
        a.unlink()
    if mutation == "add":
        _, cached, pending, _ = _selected(root, mapping, authority, ledger)
        assert set(cached) == {"a", "b"} and not pending
    else:
        _, cached, pending, _ = _selected(root, mapping, authority, ledger)
        assert list(cached) == ["b"] and pending == ["a"]



def _real_scenario_fixture(root: Path):
    root, _, authority = _demo(root)
    prefix = "openspec/changes/demo/specs/spec.md::"
    ids = [prefix + "A", prefix + "B"]
    mapping = {ids[0]: "tests/test_a.py::test_a",
               ids[1]: "tests/test_b.py::test_b"}
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text(
        "### Requirement: Coverage\n"
        "#### Scenario: A\n"
        "- **WHEN** event A\n"
        "- **THEN** outcome A\n"
        "\n"
        "#### Scenario: B\n"
        "- **WHEN** event B\n"
        "- **THEN** outcome B\n",
        encoding="utf-8",
    )
    plan = root / "openspec/changes/demo/scenario-coverage/plan.md"
    plan.write_text(
        "- SCENARIO_ID: " + ids[0] + "\n"
        "  REQUIRED_EVIDENCE: scenario_test\n"
        "- SCENARIO_ID: " + ids[1] + "\n"
        "  REQUIRED_EVIDENCE: scenario_test\n",
        encoding="utf-8",
    )
    matrix = trace_matrix_path(root / "openspec/changes/demo/scenario-coverage")
    matrix.write_text(json.dumps({
        "entries": [
            {"scenario_id": sid, "test_node_id": node}
            for sid, node in mapping.items()
        ],
    }), encoding="utf-8")
    return root, mapping, authority, ids


def test_edit_one_real_spec_scenario_keeps_sibling_receipt(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    before, ledger = _baseline(root, mapping, authority, supported=tuple(ids))
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("outcome A", "changed outcome A"),
        encoding="utf-8",
    )
    after, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert after["shared_digest"] == before["shared_digest"]
    assert after["row_digests"][ids[0]] != before["row_digests"][ids[0]]
    assert after["row_digests"][ids[1]] == before["row_digests"][ids[1]]
    assert list(reused) == [ids[1]] and pending == [ids[0]]


def test_edit_one_real_plan_entry_keeps_sibling_receipt(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    _, ledger = _baseline(root, mapping, authority, supported=tuple(ids))
    plan = root / "openspec/changes/demo/scenario-coverage/plan.md"
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "REQUIRED_EVIDENCE: scenario_test",
            "REQUIRED_EVIDENCE: explicit_path",
            1,
        ),
        encoding="utf-8",
    )
    _, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert set(reused) == set(ids) and not pending


def test_unrelated_spec_text_and_production_change_keep_both_rows(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    before, ledger = _baseline(root, mapping, authority, supported=tuple(ids))
    spec = root / "openspec/changes/demo/specs/spec.md"
    spec.write_text(
        "Intro changed independently\n" + spec.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "src/stroy/app.py").write_text("def f(): return 2\n", encoding="utf-8")
    after, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert before["row_digests"] == after["row_digests"]
    assert set(reused) == set(ids) and not pending


def test_changed_test_file_invalidates_every_scenario_mapped_to_it(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    mapping[ids[1]] = "tests/test_a.py::test_b"
    _, ledger = _baseline(root, mapping, authority, supported=tuple(ids))
    (root / "tests/test_a.py").write_text(
        "def test_a():\n    assert False\n"
        "def test_b():\n    assert True\n",
        encoding="utf-8",
    )
    _, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert not reused and pending == ids


def test_model_policy_tier_and_mapping_are_independent_row_inputs(tmp_path):
    root, mapping, authority, ids = _real_scenario_fixture(tmp_path)
    before, ledger = _baseline(root, mapping, authority, supported=tuple(ids))
    changed = {**authority, "verifier_contract_digest": "new-policy"}
    after, reused, pending, _ = _selected(root, mapping, changed, ledger)
    assert after["shared_digest"] == before["shared_digest"]
    assert set(reused) == set(ids) and not pending
    mapping[ids[0]] = "tests/test_a.py::test_alternate"
    mapped, reused, pending, _ = _selected(root, mapping, authority, ledger)
    assert mapped["row_digests"][ids[0]] != before["row_digests"][ids[0]]
    assert mapped["row_digests"][ids[1]] == before["row_digests"][ids[1]]
    assert list(reused) == [ids[1]] and pending == [ids[0]]



def test_model_tier_change_does_not_invalidate_product_receipts(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    authority = {**authority, "model": {"id": "another-tier"}}
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert set(cached) == {"a", "b"} and not pending


def test_unrelated_global_plan_revision_does_not_invalidate_scenario(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    authority = {**authority, "plan_evidence_id": "different-plan"}
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert set(cached) == {"a", "b"} and not pending


@pytest.mark.parametrize("statement", [
    "from tests.test_a import test_a\n",
    "importlib.import_module(module_name)\n",
    "pytest_plugins = ['tests.test_a']\n",
])
def test_unmapped_dependencies_are_not_in_freshness_policy(tmp_path, statement):
    root, mapping, authority = _demo(tmp_path)
    test_b = root / "tests/test_b.py"
    test_b.write_text(statement + "def test_b():\n    assert 2\n", encoding="utf-8")
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    before = scope["row_digests"].copy()
    (root / "tests/test_a.py").write_text(
        "def test_a():\n    assert False\n", encoding="utf-8",
    )
    after = impact_scope(root, _paths(root), mapping, authority=authority)
    assert after["row_digests"]["a"] != before["a"]
    assert after["row_digests"]["b"] == before["b"]


def test_unrelated_git_commit_does_not_invalidate(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    prior, ledger = _baseline(root, mapping, authority)
    selected_before = _paths(root)
    for command in (
        ["git", "init"], ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "add", "."], ["git", "commit", "-m", "baseline"],
        ["git", "commit", "--allow-empty", "-m", "unrelated"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    assert _paths(root) == selected_before
    assert not any(".git" in path.relative_to(root).parts for path in _paths(root))
    scope, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert scope == prior and len(cached) == 2 and not pending


def test_duplicate_row_receipt_disables_entire_reuse(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    ledger["rows"].append(ledger["rows"][0].copy())
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert not cached and pending == ["a", "b"]


@pytest.mark.parametrize("field", ["origin_execution_id", "evidence_id"])
def test_unbound_row_provenance_cannot_reuse(tmp_path, field):
    root, mapping, authority = _demo(tmp_path)
    _, ledger = _baseline(root, mapping, authority)
    del ledger["rows"][0][field]
    _, cached, pending, _ = _selected(root, mapping, authority, ledger)
    assert "a" not in cached and "a" in pending


def test_incomplete_first_audit_cannot_publish_rows(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    with pytest.raises(ValueError, match="ROW_LEDGER_INCOMPLETE"):
        ledger_from_validated_rows(
            plan_ids=list(mapping), current_scope=scope,
            row_reviews=[{"scenario_id": "a", "assessment": "SUPPORTED"}],
            provenance={"a": _origin()},
        )


def test_unbound_first_audit_cannot_publish_supported_row(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    scope = impact_scope(root, _paths(root), mapping, authority=authority)
    with pytest.raises(ValueError, match="ROW_EVIDENCE_UNBOUND"):
        ledger_from_validated_rows(
            plan_ids=["a"], current_scope=scope,
            row_reviews=[{"scenario_id": "a", "assessment": "SUPPORTED"}],
            provenance={},
        )


def test_production_file_deletion_does_not_invalidate_coverage_row(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    before = impact_scope(root, _paths(root), mapping, authority=authority)
    (root / "src/stroy/app.py").unlink()
    after = impact_scope(root, _paths(root), mapping, authority=authority)
    assert before == after


def test_untrusted_symlink_fails_closed(tmp_path):
    root, mapping, authority = _demo(tmp_path)
    p = root / "tests/test_a.py"
    p.unlink()
    outside = tmp_path.parent / "untrusted-row-file.py"
    outside.write_text("assert False\n", encoding="utf-8")
    try:
        p.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="(ROW_TEST_SCOPE_INVALID|SCENARIO_TEST_SCOPE_INVALID)"):
        impact_scope(root, _paths(root) | {p}, mapping, authority=authority)


def test_aggregate_exact_plan_order_and_no_duplicates():
    rows = assemble_rows(
        plan_ids=["a", "b"], cached={"b": {"scenario_id": "b"}},
        audited_ids=["a"], audited_rows=[{"scenario_id": "a"}],
    )
    assert [x["scenario_id"] for x in rows] == ["a", "b"]


@pytest.mark.parametrize("cached,audited_ids,rows", [
    ({"a": {"scenario_id": "a"}}, [], []),
    ({}, ["a", "a"], [{"scenario_id": "a"}, {"scenario_id": "a"}]),
    ({"a": {"scenario_id": "a"}}, ["a", "b"], [{"scenario_id": "a"}, {"scenario_id": "b"}]),
    ({}, ["b", "a"], [{"scenario_id": "a"}, {"scenario_id": "b"}]),
])
def test_aggregate_rejects_missing_duplicate_overlapping_or_out_of_order(cached, audited_ids, rows):
    with pytest.raises(ValueError, match="DELTA_"):
        assemble_rows(
            plan_ids=["a", "b"], cached=cached,
            audited_ids=audited_ids, audited_rows=rows,
        )
