"""Детерминированная нормализация имён scenario-coverage файлов."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.common.scenario_files import (
    file_sha256,
    reconcile_scenario_filenames,
    scenario_digest,
    scenario_filename,
)


def _fenced_body(scenario_id: str) -> str:
    return (
        "# Scenario\n\n```text\n"
        f"SCENARIO_ID: {scenario_id}\n"
        "LIFECYCLE: L1\n```\n"
    )


@pytest.mark.unit
def test_reconcile_collapses_content_hash_duplicate(tmp_path: Path) -> None:
    """Ошибочное имя (content_hash) удаляется, остаётся sha256(scenario_id)."""
    sid = "openspec/changes/demo/specs/x/spec.md::alpha"
    change = tmp_path / "openspec" / "changes" / "demo" / "scenario-coverage"
    scenarios = change / "scenarios"
    scenarios.mkdir(parents=True)
    canonical = scenarios / scenario_filename(sid)
    wrong_name = file_sha256(_fenced_body(sid).encode("utf-8")) + ".md"
    wrong = scenarios / wrong_name
    canonical.write_text(_fenced_body(sid), encoding="utf-8")
    wrong.write_text(_fenced_body(sid), encoding="utf-8")
    plan = change / "plan.md"
    plan.write_text(
        "## SCENARIO_INVENTORY\n\n```text\n"
        f"openspec/changes/demo/scenario-coverage/scenarios/{wrong_name}\n"
        "```\n",
        encoding="utf-8",
    )
    ids = reconcile_scenario_filenames(change)
    assert ids == [sid]
    assert canonical.is_file()
    assert not wrong.is_file()
    assert scenario_filename(sid) in plan.read_text(encoding="utf-8")
    assert wrong_name not in plan.read_text(encoding="utf-8")


@pytest.mark.unit
def test_reconcile_renames_lone_misnamed_file(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/x/spec.md::beta"
    change = tmp_path / "openspec" / "changes" / "demo" / "scenario-coverage"
    scenarios = change / "scenarios"
    scenarios.mkdir(parents=True)
    body = _fenced_body(sid)
    wrong = scenarios / (file_sha256(body.encode("utf-8")) + ".md")
    wrong.write_text(body, encoding="utf-8")
    reconcile_scenario_filenames(change)
    canonical = scenarios / scenario_filename(sid)
    assert canonical.is_file()
    assert not wrong.is_file()
    assert scenario_digest(sid) == canonical.stem


@pytest.mark.unit
def test_reconcile_raises_when_scenario_id_missing(tmp_path: Path) -> None:
    change = tmp_path / "openspec" / "changes" / "demo" / "scenario-coverage"
    scenarios = change / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / ("deadbeef" * 8 + ".md")).write_text("# empty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SCENARIO_ID missing"):
        reconcile_scenario_filenames(change)