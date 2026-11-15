"""repair_task_id reload keeps ledger path; new finding resets state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows.repair.schemas import FindingReport, RepairState
from workflows.stop_errors import OpsxStageStop
from workflows.verify.check_repair_outcome import compute_repair_task_id
from workflows.repair.control import stable_finding_id
from workflows.verify.repair_task_persistence import (
    _OWNER_RETRY_CAP,
    save_latest_repair_state,
    try_reload_repair_state,
    write_repair_task_meta,
)


def _base(report: FindingReport) -> RepairState:
    return RepairState.model_validate(
        {
            "mission_id": "verify-pbh-x",
            "repository_path": ".",
            "workspace": ".",
            "output_dir": str(Path("ignored")),
            "source_review": report.model_dump(mode="json"),
        },
    )


def test_reload_merges_finished_for_same_task_id(tmp_path: Path) -> None:
    report = FindingReport.model_validate(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "id": "F-1",
                    "severity": "high",
                    "location": "loc",
                    "finding": "bug",
                },
            ],
        },
    )
    base = _base(report)
    base = base.model_copy(update={"output_dir": str(tmp_path), "revision": 3})
    save_latest_repair_state(base)
    loaded = try_reload_repair_state(tmp_path, report, base=base)
    assert loaded.revision == 3
    assert loaded.mission_id == base.mission_id


def test_owner_retry_resets_seen_after_complete_no_repair(tmp_path: Path) -> None:
    finding = "Сломана последняя миграция"
    loc = finding
    fid = stable_finding_id(finding, loc, prefix="F-ROOT")
    report = FindingReport.model_validate(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "id": None,
                    "severity": "medium",
                    "location": loc,
                    "finding": finding,
                },
            ],
        },
    )
    base = _base(report).model_copy(
        update={"output_dir": str(tmp_path), "owner_go": True},
    )
    stale = base.model_copy(
        update={
            "status": "COMPLETE_NO_REPAIR",
            "current_stage": "finish",
            "queue": [
                {
                    "id": fid,
                    "finding": finding,
                    "location": loc,
                    "severity": "medium",
                    "root_relation": "ROOT",
                    "evidence_paths": [],
                },
            ],
            "seen": [fid],
            "finished": [
                {
                    "finding": {
                        "id": fid,
                        "finding": finding,
                        "location": loc,
                        "severity": "medium",
                    },
                    "terminal_state": "BLOCKED_INCONCLUSIVE",
                },
            ],
        },
    )
    repair_task_id = compute_repair_task_id(report)
    write_repair_task_meta(tmp_path, repair_task_id=repair_task_id)
    save_latest_repair_state(stale)
    loaded = try_reload_repair_state(tmp_path, report, base=base)
    assert loaded.status == "CREATED"
    assert fid not in loaded.seen
    assert loaded.queue and loaded.queue[0].id == fid


def test_owner_retry_skipped_when_fix_committed_green_blocked_on_wire(
    tmp_path: Path,
) -> None:
    finding = "visual cover regression"
    loc = "test_screen_visual_map"
    fid = stable_finding_id(finding, loc, prefix="F-ROOT")
    report = FindingReport.model_validate(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "id": fid,
                    "severity": "medium",
                    "location": loc,
                    "finding": finding,
                },
            ],
        },
    )
    base = _base(report).model_copy(
        update={"output_dir": str(tmp_path), "owner_go": True},
    )
    queue_item = {
        "id": fid,
        "finding": finding,
        "location": loc,
        "severity": "medium",
        "root_relation": "ROOT",
        "evidence_paths": [],
    }
    stale = base.model_copy(
        update={
            "status": "BLOCKED",
            "current_stage": "finish",
            "error": "GREEN_STATUS_BLOCKED",
            "queue": [queue_item],
            "seen": [fid],
            "finished": [],
            "fix": {
                "status": "FIXED",
                "summary": "patched",
                "files_changed": ["src/example.py"],
                "execution_route": "LOCAL_REPAIR",
                "patch_hash": "p1",
            },
            "commit_status": "COMMITTED",
            "commit_sha": "706ac4fa",
            "green": {
                "status": "BLOCKED",
                "test_id": "tests/unit/x.py::test_y",
                "notes": "run_pytest MCP unavailable",
            },
        },
    )
    stale = RepairState.model_validate(stale.model_dump(mode="json"))
    repair_task_id = compute_repair_task_id(report)
    write_repair_task_meta(tmp_path, repair_task_id=repair_task_id)
    save_latest_repair_state(stale)
    loaded = try_reload_repair_state(tmp_path, report, base=base)
    assert loaded.status == "BLOCKED"
    assert fid in loaded.seen


def test_owner_retry_cap_raises_opsx_stage_stop(tmp_path: Path) -> None:
    finding = "Сломана последняя миграция"
    loc = finding
    fid = stable_finding_id(finding, loc, prefix="F-ROOT")
    report = FindingReport.model_validate(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "id": None,
                    "severity": "medium",
                    "location": loc,
                    "finding": finding,
                },
            ],
        },
    )
    base = _base(report).model_copy(
        update={"output_dir": str(tmp_path), "owner_go": True},
    )
    stale = base.model_copy(
        update={
            "status": "COMPLETE_NO_REPAIR",
            "current_stage": "finish",
            "queue": [
                {
                    "id": fid,
                    "finding": finding,
                    "location": loc,
                    "severity": "medium",
                    "root_relation": "ROOT",
                    "evidence_paths": [],
                },
            ],
            "seen": [fid],
            "finished": [],
        },
    )
    repair_task_id = compute_repair_task_id(report)
    write_repair_task_meta(tmp_path, repair_task_id=repair_task_id)
    meta_path = tmp_path / "repair_task_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["owner_retry_count"] = _OWNER_RETRY_CAP
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_latest_repair_state(stale)
    with pytest.raises(OpsxStageStop) as err:
        _ = try_reload_repair_state(tmp_path, report, base=base)
    assert err.value.code == "OWNER_RETRY_CAP"


def test_fix_changes_finding_id_new_mission(tmp_path: Path) -> None:
    payload = {
        "status": "FINDINGS",
        "findings": [
            {
                "id": "F-1",
                "severity": "medium",
                "location": "a",
                "finding": "bug",
            },
        ],
    }
    r1 = FindingReport.model_validate(payload)
    payload["findings"][0]["id"] = "F-2"
    payload["findings"][0]["location"] = "b"
    r2 = FindingReport.model_validate(payload)
    assert compute_repair_task_id(r1) != compute_repair_task_id(r2)
