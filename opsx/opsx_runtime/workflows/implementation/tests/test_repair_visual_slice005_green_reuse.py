"""Регрессии: GREEN re-run, RED shaping, receipt reuse guards (SLICE-005)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from check_workflow_receipt import CheckWorkflowReceiptStatus

from workflows.implementation.routing_contract import STAGE_APPLY_RED, STAGE_APPLY_SCAFFOLD
from workflows.implementation.slice_check_workflow_receipt import (
    WORK_SCOPE_DIGEST_KEY,
    evaluate_slice_stage,
    persist_after_successful_hop,
)
from workflows.implementation.slice_receipt import (
    load_stage_receipt,
    receipt_json_path,
    stage_scope_overlay_path,
)
from workflows.implementation.stage_receipt_writer import (
    _historical_red_executions_from_frozen_red,
    _red_final_status_from_executions,
    _run_green_verification_executions,
    write_green_receipt_from_hop,
)
from workflows.implementation.stage_red_receipt_shaper import shape_red_receipt_after_hop
from workflows.repo_paths import resolve_opsx_repository_root


def _ensure_opsx_root(repo: Path) -> None:
    _ = (repo / "opsx" / "project").mkdir(parents=True, exist_ok=True)


def _seed_green_predecessor_pair(
    repo: Path,
    *,
    red_exit: int,
    test_id: str = "f" * 64,
) -> tuple[str, str]:
    """Минимальные RED+IMPLEMENT в openspec для write_green_receipt_from_hop."""
    _ensure_opsx_root(repo)
    change = "green-regression-hop"
    slice_id = "SLICE-001"
    rel = "tests/green_regression_hop.py"
    test_file = repo / rel
    test_file.parent.mkdir(parents=True, exist_ok=True)
    _ = test_file.write_text("def test_green_regression_hop(): assert True\n", encoding="utf-8")
    cmd = f"poetry run pytest -q {rel}"
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    impl_dir = repo / "openspec" / "changes" / change / "apply-implement" / slice_id
    red_dir.mkdir(parents=True)
    impl_dir.mkdir(parents=True)
    red: dict[str, Any] = {
        "change_id": change,
        "slice_id": slice_id,
        "schema": "opsx.apply_red",
        "schema_version": 2,
        "status": "RED_PROVEN",
        "chat_history_required": False,
        "test_paths": [rel],
        "fixture_paths": [],
        "test_commands": [cmd],
        "test_content_identity": test_id,
        "command_executions": [
            {
                "command": cmd,
                "final_exit_code": red_exit,
                "attempts": [{"exit_code": red_exit, "relevant_output": "historical fail"}],
            },
        ],
    }
    implement: dict[str, Any] = {
        "change_id": change,
        "slice_id": slice_id,
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "status": "IMPLEMENTATION_WRITTEN",
        "chat_history_required": False,
        "frozen_test_content_identity": test_id,
        "tests_modified": False,
        "product_files_modified": True,
        "production_paths_modified": ["src/stroy/example.py"],
        "red_identity": {
            "slice_id": slice_id,
            "status": "RED_PROVEN",
            "test_content_identity": test_id,
            "tests_frozen": True,
        },
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(red, indent=2), encoding="utf-8")
    _ = (impl_dir / "IMPLEMENTATION.json").write_text(json.dumps(implement, indent=2), encoding="utf-8")
    return change, slice_id


def test_historical_red_keeps_nonzero_while_green_reruns_pass() -> None:
    """Исторический RED FAIL не затирается; GREEN verification — свежие exit 0."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    red_fail = {
        "test_commands": [
            "poetry run pytest -q tests/unit/channels/telegram/invest_yalta/test_pending_confirmed_covers.py::test_pending_uses_cover_pending_received",
        ],
        "command_executions": [{"final_exit_code": 1}],
    }
    historical = _historical_red_executions_from_frozen_red(red_fail)
    assert historical[0]["exit_code"] == 1
    assert historical[0]["phase"] == "historical"
    live = _run_green_verification_executions(repo, red_fail["test_commands"])
    assert live
    assert all(int(e.get("exit_code", 1)) == 0 for e in live)
    assert live[0]["phase"] == "green_verify"
    assert _red_final_status_from_executions(live) == "PASS"
    assert _red_final_status_from_executions(historical) == "FAIL"


def test_durable_red_stub_without_operative_pass_never_skips(tmp_path: Path) -> None:
    """Минимальный ALREADY_SATISFIED без PASS ``opsx-apply-red.mjs`` не даёт SKIP."""
    change = "red-stub-skip"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "tests/t.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("def test_x(): pass\n", encoding="utf-8")
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    _ = (red_dir / "RED.json").write_text(
        json.dumps({"status": "ALREADY_SATISFIED", "test_paths": [rel], "fixture_paths": []}),
        encoding="utf-8",
    )
    ev = evaluate_slice_stage(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert not ev.valid
    assert ev.code == "RECEIPT_NOT_ACCEPTED"


def test_fingerprint_match_rejected_when_operative_fails(tmp_path: Path) -> None:
    """SCOPE MATCH не даёт SKIP, если operative contract не принят."""
    change = "reuse-guard"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "tests/bad_red.py"
    test_file = repo / rel
    test_file.parent.mkdir(parents=True)
    _ = test_file.write_text(
        '# @opsx-red-spec-value: X\n# @opsx-red-behavior-value: NOT-MET\n',
        encoding="utf-8",
    )
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    bad = {
        "schema": "opsx.apply_red",
        "status": "RED_PROVEN",
        "test_paths": [rel],
        "test_commands": ["poetry run pytest -q " + rel],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(bad), encoding="utf-8")
    second = evaluate_slice_stage(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert not second.valid
    assert second.code in {"RECEIPT_NOT_ACCEPTED", "SCOPE_STALE"}


def test_active_repair_blocks_digest_reuse(tmp_path: Path) -> None:
    change = "repair-block"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "pkg/a.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("ok", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "status": "SCAFFOLD_READY",
                "extended_paths": [rel],
                "created_paths": [],
            },
        ),
        encoding="utf-8",
    )
    ready = persist_after_successful_hop(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
    )
    assert ready.valid
    blocked = evaluate_slice_stage(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state={"implementation_hop_machine_repair_count": 1},
    )
    assert not blocked.valid
    assert blocked.code == "ACTIVE_REPAIR_BLOCKS_REUSE"


def test_persist_after_hop_valid_when_active_repair_blocks_pre_skip(tmp_path: Path) -> None:
    """POST после persist valid при repair; PRE/skip по-прежнему ACTIVE_REPAIR_BLOCKS_REUSE."""
    change = "repair-post-persist"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "pkg/post_persist.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("ok", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "status": "SCAFFOLD_READY",
                "extended_paths": [rel],
                "created_paths": [],
            },
        ),
        encoding="utf-8",
    )
    repair_state = {"implementation_hop_machine_repair_count": 1}
    post = persist_after_successful_hop(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state=repair_state,
    )
    assert post.valid
    assert post.receipt_status is CheckWorkflowReceiptStatus.MATCH
    blocked = evaluate_slice_stage(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state=repair_state,
    )
    assert not blocked.valid
    assert blocked.code == "ACTIVE_REPAIR_BLOCKS_REUSE"


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[5]
        / "openspec/changes/visual/apply-red/SLICE-005/RED.json"
    ).is_file(),
    reason="visual SLICE-005 receipt absent",
)
def test_visual_slice005_shape_and_operative_pass() -> None:
    """ALREADY_SATISFIED SLICE-005: shape stamps + operative validator PASS."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    shaped = shape_red_receipt_after_hop("visual", "SLICE-005", repo_root=repo)
    assert shaped.ok, shaped.code
    from workflows.implementation.red_operative_validation import (
        slice_red_json_path,
        validate_red_operative,
    )

    gate = validate_red_operative(
        slice_red_json_path("visual", repo_root=repo, slice_id="SLICE-005"),
        repo_root=repo,
    )
    assert gate.ok, gate.errors


def test_scope_digest_lives_in_service_overlay(tmp_path: Path) -> None:
    """``work_scope_digest`` пишется в opsx/receipts overlay, не в useful JSON."""
    change = "overlay-ch"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "pkg/x.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("v=1\n", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {"status": "SCAFFOLD_READY", "created_paths": [], "extended_paths": [rel]},
        ),
        encoding="utf-8",
    )
    first = evaluate_slice_stage(change, slice_id, "APPLY_SCAFFOLD", repo_root=repo)
    assert first.valid
    overlay = stage_scope_overlay_path(
        change,
        slice_id,
        "APPLY_SCAFFOLD",
        repo_root=repo,
    )
    assert overlay.is_file()
    overlay_body = json.loads(overlay.read_text(encoding="utf-8"))
    assert overlay_body.get(WORK_SCOPE_DIGEST_KEY)
    useful_body = json.loads((scaffold_dir / "SCAFFOLD.json").read_text(encoding="utf-8"))
    assert WORK_SCOPE_DIGEST_KEY not in useful_body
    merged = load_stage_receipt(change, slice_id, "APPLY_SCAFFOLD", repo_root=repo)
    assert merged is not None
    assert merged.get(WORK_SCOPE_DIGEST_KEY)


def test_write_green_refuses_missing_or_failing_green_verify(tmp_path: Path) -> None:
    """Пустой или failing green_verify — не PASS; writer отказывает с gate-кодом."""
    change, slice_id = _seed_green_predecessor_pair(tmp_path, red_exit=1)
    pairing_ok = {"ok": True, "branch": "NORMAL"}
    hop_answer = {
        "status": "APPLY_COMPLETE",
        "tasks_summary": "regression",
        "gap_closure_summary": "regression",
        "files_changed": [],
    }
    with patch(
        "workflows.implementation.stage_receipt_writer.classify_green_predecessor_pairing",
        return_value=pairing_ok,
    ):
        with patch(
            "workflows.implementation.stage_receipt_writer._run_green_verification_executions",
            return_value=[],
        ):
            missing = write_green_receipt_from_hop(
                change,
                slice_id,
                repo_root=tmp_path,
                answer=hop_answer,
            )
        assert missing.ok is False
        assert missing.code == "GREEN_VERIFICATION_MISSING"
        with patch(
            "workflows.implementation.stage_receipt_writer._run_green_verification_executions",
            return_value=[
                {
                    "phase": "green_verify",
                    "iteration": 0,
                    "command": "poetry run pytest -q tests/green_regression_hop.py",
                    "exit_code": 1,
                    "relevant_output": "assertion failed",
                },
            ],
        ):
            failing = write_green_receipt_from_hop(
                change,
                slice_id,
                repo_root=tmp_path,
                answer=hop_answer,
            )
        assert failing.ok is False
        assert failing.code == "RED_EVIDENCE_NOT_PASS"


def test_write_green_keeps_frozen_historical_on_red_fail_to_green_pass(tmp_path: Path) -> None:
    """RED fail → IMPLEMENT → новый green_verify PASS: historical не затирается."""
    change, slice_id = _seed_green_predecessor_pair(tmp_path, red_exit=1)
    red = load_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=tmp_path)
    assert red is not None
    expected_historical = _historical_red_executions_from_frozen_red(red)
    assert expected_historical
    assert all(int(e["exit_code"]) != 0 for e in expected_historical)
    live_pass = [
        {
            "phase": "green_verify",
            "iteration": 0,
            "command": str(red["test_commands"][0]),
            "exit_code": 0,
            "relevant_output": "ok",
        },
    ]
    green_path = tmp_path / "openspec" / "changes" / change / "apply-green" / slice_id / "GREEN.json"
    try:
        with patch(
            "workflows.implementation.stage_receipt_writer.classify_green_predecessor_pairing",
            return_value={"ok": True, "branch": "NORMAL"},
        ), patch(
            "workflows.implementation.stage_receipt_writer._run_green_verification_executions",
            return_value=live_pass,
        ):
            result = write_green_receipt_from_hop(
                change,
                slice_id,
                repo_root=tmp_path,
                answer={
                    "status": "APPLY_COMPLETE",
                    "tasks_summary": "implemented",
                    "gap_closure_summary": "green pass",
                    "files_changed": [],
                },
            )
        assert result.ok is True
        assert result.path == green_path
        green = json.loads(green_path.read_text(encoding="utf-8"))
        assert green["historical_red_executions"] == expected_historical
        assert green["red_executions"] == live_pass
        assert green["red_final_status"] == "PASS"
        assert all(e.get("phase") == "green_verify" for e in green["red_executions"])
    finally:
        if green_path.is_file():
            green_path.unlink()
        md = green_path.with_name("GREEN.md")
        if md.is_file():
            md.unlink()


def test_valid_useful_receipt_skip_without_second_authoring_hop(tmp_path: Path) -> None:
    """MATCH по digest даёт valid без повторного hop (явно в regression-файле)."""
    change = "skip-no-hop"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "tests/skip_once.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("def test_skip_once(): assert True\n", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "status": "SCAFFOLD_READY",
                "extended_paths": [rel],
                "created_paths": [],
            },
        ),
        encoding="utf-8",
    )
    post = persist_after_successful_hop(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
    )
    assert post.valid
    reuse = evaluate_slice_stage(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state=None,
    )
    assert reuse.valid
    assert reuse.receipt_status is CheckWorkflowReceiptStatus.MATCH
    overlay = stage_scope_overlay_path(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    assert overlay.is_file()
    assert "opsx/receipts" in overlay.as_posix()
    useful = scaffold_dir / "SCAFFOLD.json"
    assert useful.is_file()
    assert WORK_SCOPE_DIGEST_KEY not in json.loads(useful.read_text(encoding="utf-8"))


def test_reset_reuses_digest_match_but_active_repair_forces_rework(tmp_path: Path) -> None:
    """После reset reuse OK; при активном repair digest-match не принимается."""
    change = "repair-reset"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "pkg/repair_reset.py"
    (repo / rel).parent.mkdir(parents=True)
    _ = (repo / rel).write_text("ok", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "status": "SCAFFOLD_READY",
                "extended_paths": [rel],
                "created_paths": [],
            },
        ),
        encoding="utf-8",
    )
    post = persist_after_successful_hop(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
    )
    assert post.valid
    blocked = evaluate_slice_stage(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state={"implementation_hop_machine_repair_count": 1},
    )
    assert not blocked.valid
    assert blocked.code == "ACTIVE_REPAIR_BLOCKS_REUSE"
    assert blocked.receipt_status is CheckWorkflowReceiptStatus.MATCH
    reset = evaluate_slice_stage(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
        state=None,
    )
    assert reset.valid
    assert reset.receipt_status is CheckWorkflowReceiptStatus.MATCH


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[5]
        / "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    ).is_file(),
    reason="visual APPLY-DESIGN absent",
)
def test_python_receipt_path_matches_shape_cli_useful_red(tmp_path: Path) -> None:
    """``receipt_json_path`` и shape CLI указывают на один useful RED в openspec."""
    repo = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
    change = "visual"
    slice_id = "SLICE-005"
    py_path = receipt_json_path(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert "openspec/changes" in py_path.as_posix()
    overlay = stage_scope_overlay_path(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert overlay.as_posix().startswith((repo / "opsx/receipts").as_posix())
    script = repo / "opsx_tools" / "opsx-shape-slice-red-receipt-cli.mjs"
    assert script.is_file()
    run = subprocess.run(
        [
            "node",
            str(script),
            "--repo",
            str(repo),
            "--change",
            change,
            "--slice",
            slice_id,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    body = json.loads(run.stdout.strip().splitlines()[-1])
    assert body.get("ok") is True
    cli_rel = str(body.get("path") or "")
    assert cli_rel.replace("\\", "/") == py_path.relative_to(repo).as_posix()
