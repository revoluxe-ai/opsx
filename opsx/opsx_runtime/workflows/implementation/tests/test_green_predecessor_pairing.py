"""Регрессия pairing ALREADY_SATISFIED + IMPLEMENTATION_WRITTEN для GREEN receipt."""

from __future__ import annotations

import json
from pathlib import Path

from workflows.implementation.slice_stage_operative_validation import validate_green_operative
from workflows.implementation.stage_receipt_writer import (
    _red_executions_from_frozen_red,
    _red_final_status_from_executions,
    classify_green_predecessor_pairing,
    repair_visual_slice_004_false_green,
    write_green_receipt_from_hop,
)
from workflows.repo_paths import resolve_opsx_repository_root


def _visual_slice_004_triplet(repo: Path) -> tuple[dict[str, object], dict[str, object]]:
    """Фикстура visual SLICE-004: ALREADY_SATISFIED RED + IMPLEMENTATION_WRITTEN."""
    red_path = repo / "tests/unit/fixtures/visualSlice004StaleRed/RED.json"
    red = json.loads(red_path.read_text(encoding="utf-8"))
    test_id = str(red["test_content_identity"])
    implement: dict[str, object] = {
        "status": "IMPLEMENTATION_WRITTEN",
        "slice_id": "SLICE-004",
        "next_stage": "APPLY_GREEN",
        "tests_modified": False,
        "tests_executed": False,
        "frozen_test_content_identity": test_id,
        "red_identity": {
            "status": "ALREADY_SATISFIED",
            "slice_id": "SLICE-004",
            "test_content_identity": test_id,
            "tests_frozen": True,
        },
        "production_paths_modified": ["src/stroy/channels/max/invest_yalta/max_present.py"],
    }
    return red, implement


def test_classify_already_satisfied_write_pairing_ok() -> None:
    """Классификатор принимает связку already-satisfied + implementation written."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    red, implement = _visual_slice_004_triplet(repo)
    pairing = classify_green_predecessor_pairing(
        repo_root=repo,
        red=red,
        implement=implement,
    )
    assert pairing.get("ok") is True
    assert pairing.get("branch") == "ALREADY_SATISFIED_WRITE"


def test_write_green_semantic_fields_pass_operative_gate_visual_slice_004() -> None:
    """Deterministic writer emits fields required by opsx-apply-green semantic gate."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    red_path = repo / "opsx/receipts/visual/apply-red/SLICE-004/RED.json"
    imp_path = repo / "opsx/receipts/visual/apply-implement/SLICE-004/IMPLEMENTATION.json"
    assert red_path.is_file() and imp_path.is_file()
    _ = repair_visual_slice_004_false_green(repo_root=repo)
    result = write_green_receipt_from_hop(
        "visual",
        "SLICE-004",
        repo_root=repo,
        answer={
            "status": "APPLY_COMPLETE",
            "tasks_summary": "regression",
            "gap_closure_summary": "regression",
            "files_changed": [],
        },
    )
    assert result.ok is True
    assert result.path is not None
    green = json.loads(result.path.read_text(encoding="utf-8"))
    assert green.get("chat_history_required") is False
    assert green.get("red_final_status") == "PASS"
    assert green.get("frozen_test_content_identity") == green.get("frozen_test_identity_before")
    gate = validate_green_operative(result.path, repo_root=repo)
    assert gate.ok is True, gate.errors
    _ = repair_visual_slice_004_false_green(repo_root=repo)


def test_red_final_status_fails_when_frozen_execution_nonzero() -> None:
    """Ненулевой exit в frozen RED — red_final_status FAIL, writer gate откажет."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    red = json.loads(
        (repo / "opsx/receipts/visual/apply-red/SLICE-004/RED.json").read_text(encoding="utf-8"),
    )
    red_fail = dict(red)
    command_execs = list(red_fail.get("command_executions") or [])
    if command_execs and isinstance(command_execs[0], dict):
        command_execs[0] = {**command_execs[0], "final_exit_code": 1}
        red_fail["command_executions"] = command_execs
    executions = _red_executions_from_frozen_red(red_fail)
    assert _red_final_status_from_executions(executions) == "FAIL"


def test_write_green_refuses_when_predecessor_pairing_breaks() -> None:
    """Повреждённый on-disk RED ломает pairing — GREEN не пишется."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    tmp_red = repo / "opsx/receipts/visual/apply-red/SLICE-004/RED.json"
    backup = tmp_red.read_text(encoding="utf-8")
    red = json.loads(backup)
    red["test_content_identity"] = "0000000000000000000000000000000000000000000000000000000000000000"
    tmp_red.write_text(json.dumps(red, indent=2) + "\n", encoding="utf-8")
    try:
        result = write_green_receipt_from_hop(
            "visual",
            "SLICE-004",
            repo_root=repo,
            answer={
                "status": "APPLY_COMPLETE",
                "tasks_summary": "stale",
                "gap_closure_summary": "stale",
                "files_changed": [],
            },
        )
        assert result.ok is False
        assert result.code
    finally:
        tmp_red.write_text(backup, encoding="utf-8")
        _ = repair_visual_slice_004_false_green(repo_root=repo)


def test_write_green_refuses_pairing_when_implement_missing() -> None:
    """Без IMPLEMENTATION на диске deterministic writer не пишет GREEN."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    result = write_green_receipt_from_hop(
        "visual",
        "SLICE-404-MISSING",
        repo_root=repo,
        answer={
            "status": "APPLY_PARTIAL",
            "tasks_summary": "t",
            "gap_closure_summary": "g",
            "files_changed": [],
        },
    )
    assert result.ok is False
    assert result.code == "UPSTREAM_RECEIPT_MISSING"
