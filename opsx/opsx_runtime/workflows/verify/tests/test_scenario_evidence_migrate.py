"""Тесты детерминированной миграции scenario evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.common.scenario_evidence_migrate import (
    _remap_proofs,
    migrate_change_evidence,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    row_journal_path,
    trace_matrix_path,
)
from agents.common.scenario_files import file_sha256, scenario_filename, scenario_test_relpath
from agents.verify.scenario_coverage_verify.row_disposition import (
    build_scenario_coverage_row_context,
    resolve_row_work,
)
from agents.verify.scenario_coverage_verify.row_receipts import save_supported_row
from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageRowReview


def _write_min_change(tmp_path: Path, change_id: str, scenario_id: str) -> Path:
    base = tmp_path / "openspec" / "changes" / change_id
    specs = base / "specs" / "demo" / "spec.md"
    specs.parent.mkdir(parents=True, exist_ok=True)
    specs.write_text(
        "# Demo\n\n## Scenario: Alpha\n\n- **WHEN** user acts\n- **THEN** outcome holds\n",
        encoding="utf-8",
    )
    scenario_id = f"openspec/changes/{change_id}/specs/demo/spec.md::Alpha"
    coverage = base / "scenario-coverage"
    coverage.mkdir(parents=True)
    ensure_machine_receipt_dirs(coverage)
    (coverage / "scenarios").mkdir()
    body = f"    - SCENARIO_ID: {scenario_id}\n      PRIORITY: P0\n"
    from agents.common.scenario_files import scenario_filename

    (coverage / "scenarios" / scenario_filename(scenario_id)).write_text(body, encoding="utf-8")
    (coverage / "plan.md").write_text("# Plan\n", encoding="utf-8")
    rel_test = scenario_test_relpath(change_id, scenario_id)
    test_path = tmp_path / rel_test
    test_path.parent.mkdir(parents=True, exist_ok=True)
    old_test = tmp_path / "tests" / "integration" / "legacy_alpha.py"
    old_test.parent.mkdir(parents=True)
    old_test.write_text(
        '''async def test_alpha():
    await setup()
    assert True
    assert outcome()
''',
        encoding="utf-8",
    )
    test_path.write_text(
        f'''"""doc.

SCENARIO_ID: {scenario_id}
"""
async def test_alpha():
    """Смысл: проверка.

SCENARIO_ID: {scenario_id}"""
    await setup()
    assert True
    assert outcome()
''',
        encoding="utf-8",
    )
    node = f"{rel_test}::test_alpha"
    trace_matrix_path(coverage).write_text(
        json.dumps(
            {
                "entries": [{"scenario_id": scenario_id, "test_node_id": node}],
            },
        ),
        encoding="utf-8",
    )
    scenario_path = coverage / "scenarios" / scenario_filename(scenario_id)
    plan_receipt_path(coverage).write_text(
        json.dumps(
            {
                "PLAN_EVIDENCE_ID": "plan-x",
                "SCENARIO_COUNT": 1,
                "SCENARIO_FILES": [
                    {
                        "scenario_id": scenario_id,
                        "path": f"scenarios/{scenario_filename(scenario_id)}",
                        "content_hash": file_sha256(scenario_path.read_bytes()),
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    journal = {
        scenario_id: {
            "recheck": {
                "row_reviews": [
                    {
                        "scenario_id": scenario_id,
                        "test_node_id": "tests/integration/legacy_alpha.py::test_alpha",
                        "assessment": "SUPPORTED",
                        "evidence": "ok",
                        "when_proofs": [2],
                        "outcome_proofs": [3, 4],
                    },
                ],
            },
        },
    }
    row_journal_path(coverage).write_text(json.dumps(journal), encoding="utf-8")
    return tmp_path


def test_remap_proofs_docstring_offset(tmp_path: Path) -> None:
    """Сдвиг номеров строк при том же теле и новом module docstring."""
    (tmp_path / "tests").mkdir()
    old = tmp_path / "tests" / "a.py"
    new = tmp_path / "tests" / "b.py"
    old.write_text("async def test_x():\n    await step()\n    assert ok\n", encoding="utf-8")
    new.write_text(
        '"""module."""\nasync def test_x():\n    await step()\n    assert ok\n',
        encoding="utf-8",
    )
    remapped = _remap_proofs(
        tmp_path,
        old_node="tests/a.py::test_x",
        new_node="tests/b.py::test_x",
        when_proofs=[2],
        outcome_proofs=[3],
    )
    assert remapped == ([3], [4])


@pytest.fixture(autouse=True)
def _verifier_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    import agents.common.scenario_evidence_migrate as sem

    monkeypatch.setattr(
        sem,
        "scenario_verifier_contract_digest",
        lambda *_a, **_k: "0" * 64,
    )
    monkeypatch.setattr(sem, "check_coverage", lambda *_a, **_k: [])


def test_migrate_sets_resolve_row_worktled(tmp_path: Path) -> None:
    change_id = "demo-migrate"
    scenario_id = f"openspec/changes/{change_id}/specs/demo/spec.md::Alpha"
    repo = _write_min_change(tmp_path, change_id, scenario_id)
    report = migrate_change_evidence(repo, change_id, dry_run=False)
    assert report.migrated_supported == 1
    assert report.repair_proof_remap == 0
    ctx = build_scenario_coverage_row_context(
        repo,
        change_id,
        authority={
            "model": {"id": "composer-2.5", "options": []},
            "plan_evidence_id": "plan-x",
            "verifier_contract_digest": "0" * 64,
        },
    )
    work = resolve_row_work(ctx, scenario_id, disposition_mode="walk")
    assert work.run_check_repair_cycle is False
    assert work.reason == "FRESH_SUPPORTED_RECEIPT"


def test_unremappable_skipped_without_write(tmp_path: Path) -> None:
    change_id = "demo-skip"
    scenario_id = f"openspec/changes/{change_id}/specs/demo/spec.md::Alpha"
    repo = _write_min_change(tmp_path, change_id, scenario_id)
    rel = scenario_test_relpath(change_id, scenario_id)
    (repo / rel).write_text("def test_alpha():\n    pass\n", encoding="utf-8")
    journal_path = row_journal_path(repo / "openspec/changes" / change_id / "scenario-coverage")
    journal = json.loads(journal_path.read_text())
    journal[scenario_id]["recheck"]["row_reviews"][0]["when_proofs"] = [999]
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    report = migrate_change_evidence(repo, change_id, dry_run=True)
    assert report.repair_proof_remap == 1
