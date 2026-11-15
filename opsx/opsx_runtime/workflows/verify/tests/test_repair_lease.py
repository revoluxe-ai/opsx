"""Unit tests for orphan repair lease reconcile."""
from __future__ import annotations

import json
from pathlib import Path

from agents.verify.scenario_coverage_verify.repair_lease import (
    OpenRepairDecision,
    any_repair_still_running,
    latest_repair_sdk_status,
    repair_dialog_root,
    resolve_open_repair,
)


def _repair_meta(tmp_path: Path, change_id: str, run_uuid: str, sdk_status: str) -> Path:
    root = repair_dialog_root(tmp_path, change_id, run_uuid)
    root.mkdir(parents=True)
    meta = root / "meta.json"
    meta.write_text(json.dumps({"sdk_status": sdk_status, "run_uuid": run_uuid}), encoding="utf-8")
    return meta


def test_latest_repair_sdk_status_missing_dir(tmp_path: Path) -> None:
    root = repair_dialog_root(tmp_path, "demo", "run-1")
    assert latest_repair_sdk_status(root) == "missing"


def test_latest_repair_sdk_status_picks_newest_revision(tmp_path: Path) -> None:
    root = repair_dialog_root(tmp_path, "demo", "run-1")
    root.mkdir(parents=True)
    (root / "meta.json").write_text(json.dumps({"sdk_status": "finished"}), encoding="utf-8")
    rev = root / "revisions" / "run-abc"
    rev.mkdir(parents=True)
    rev_meta = rev / "meta.json"
    rev_meta.write_text(json.dumps({"sdk_status": "running"}), encoding="utf-8")
    assert latest_repair_sdk_status(root) == "running"


def test_any_repair_still_running_scans_all_runs(tmp_path: Path) -> None:
    _repair_meta(tmp_path, "demo", "finished-run", "finished")
    _repair_meta(tmp_path, "demo", "active-run", "running")
    assert any_repair_still_running(tmp_path, "demo") is True


def test_resolve_open_repair_legacy_started_without_uuid(tmp_path: Path) -> None:
    row = {"repair": "started", "token": "x"}
    resolution = resolve_open_repair(
        row, repo=tmp_path, change_id="demo", current_run_uuid="new-run",
    )
    assert resolution.decision == OpenRepairDecision.CONTINUE


def test_resolve_open_repair_pauses_when_lease_run_running(tmp_path: Path) -> None:
    _repair_meta(tmp_path, "demo", "old-run", "running")
    row = {"repair": "started", "repair_run_uuid": "old-run"}
    resolution = resolve_open_repair(
        row, repo=tmp_path, change_id="demo", current_run_uuid="new-run",
    )
    assert resolution.decision == OpenRepairDecision.PAUSE_STILL_ACTIVE
