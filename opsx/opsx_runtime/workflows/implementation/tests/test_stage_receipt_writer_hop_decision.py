"""record_hop_decision: два stage через один static method, schema-valid receipts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.implementation.apply_scaffold.scaffold_hop_visible_text import (
    scaffold_hop_visible_text_is_clean,
)
from agents.implementation.prompt.slice_receipt_hop_tool import slice_receipt_hop_tool_block
from agents.implementation.slice_receipt_tool_schema import RECORD_SLICE_RECEIPT_SCHEMA
from workflows.implementation.stage_receipt_writer import StageReceiptWriter

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCHEMA_SCAFFOLD = _REPO_ROOT / "opsx_tools" / "registry" / "apply_scaffold.schema.json"
_SCHEMA_RED = _REPO_ROOT / "opsx_tools" / "registry" / "apply_red.schema.json"
_SCHEMA_IMPLEMENT = _REPO_ROOT / "opsx_tools" / "registry" / "apply_implement.schema.json"
_SCAFFOLD_VALIDATOR = _REPO_ROOT / "opsx_tools" / "opsx-apply-scaffold.mjs"
_RED_VALIDATOR = _REPO_ROOT / "opsx_tools" / "opsx-apply-red.mjs"
_IMPLEMENT_VALIDATOR = _REPO_ROOT / "opsx_tools" / "opsx-apply-implement.mjs"


def _run_schema(
    validator: Path,
    schema: Path,
    receipt_path: Path,
    *,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    assert validator.is_file()
    cmd = ["node", str(validator), "--schema", str(schema)]
    if repo_root is not None:
        cmd.extend(["--repo", str(repo_root)])
    cmd.append(str(receipt_path))
    return subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    _ = (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "marker.txt"
    _ = marker.write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def _write_minimal_design(tmp_path: Path, change_id: str, slice_id: str) -> None:
    work = "src/pkg/widget.py"
    product = tmp_path / work
    product.parent.mkdir(parents=True, exist_ok=True)
    _ = product.write_text("def widget():\n    pass\n", encoding="utf-8")
    design_dir = tmp_path / "openspec" / "changes" / change_id / "apply-design"
    design_dir.mkdir(parents=True, exist_ok=True)
    design = {
        "schema": "opsx.apply_design",
        "schema_version": 3,
        "change_id": change_id,
        "status": "APPLY_DESIGN_READY",
        "planning_identity": {"planning_digest": "a" * 64},
        "repository_identity": {"inspected_digest": "b" * 64},
        "protected_context_identity": {"content_digest": "c" * 64},
        "slices": [
            {
                "slice_id": slice_id,
                "expected_product_write_surfaces": [work],
                "scaffold_contract": {
                    "no_op_allowed": True,
                    "required_surfaces": [
                        {
                            "path": work,
                            "action": "EXTEND",
                            "required_symbols": ["widget"],
                        }
                    ],
                    "allowed_scaffold_writes": [work],
                },
            }
        ],
    }
    _ = (design_dir / "APPLY-DESIGN.json").write_text(
        json.dumps(design, indent=2),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "design"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


@pytest.mark.unit
def test_tool_schema_minimal_parameters_only() -> None:
    props = set(RECORD_SLICE_RECEIPT_SCHEMA["properties"])
    assert props == set()
    assert RECORD_SLICE_RECEIPT_SCHEMA["required"] == []
    assert RECORD_SLICE_RECEIPT_SCHEMA["additionalProperties"] is False


@pytest.mark.unit
def test_prompt_tool_block_has_no_forbidden_paths() -> None:
    text = slice_receipt_hop_tool_block()
    assert scaffold_hop_visible_text_is_clean(text)
    assert "Do **not** edit on-disk receipt JSON" in text
    assert "record_slice_receipt" not in text
    assert ".py" not in text


@pytest.mark.unit
def test_record_hop_decision_rejects_digest_param(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    result = StageReceiptWriter.record_hop_decision(
        "hop-decision",
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_SCAFFOLD",
            "slice_id": "SLICE-001",
            "receipt_status": "BLOCKED",
            "planning_digest": "deadbeef",
        },
    )
    assert result.ok is False
    assert result.code and "FORBIDDEN" in result.code


@pytest.mark.unit
def test_scaffold_and_red_blocked_via_same_static_method(tmp_path: Path) -> None:
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)

    scaffold = StageReceiptWriter.record_from_dispatch(
        change_id,
        repo_root=tmp_path,
        stage="APPLY_SCAFFOLD",
        slice_id=slice_id,
        hop_status="BLOCKED",
        hop_answer={
            "blockers": [{"code": "INPUT_SCOPE_INCOMPLETE", "detail": "test"}],
        },
    )
    assert scaffold.ok, scaffold.code
    assert scaffold.path is not None
    scaffold_proc = _run_schema(_SCAFFOLD_VALIDATOR, _SCHEMA_SCAFFOLD, scaffold.path)
    assert scaffold_proc.returncode == 0, scaffold_proc.stdout + scaffold_proc.stderr
    body = json.loads(scaffold.path.read_text(encoding="utf-8"))
    assert body["status"] == "BLOCKED"
    assert body["schema"] == "opsx.apply_scaffold"

    red = StageReceiptWriter.record_from_dispatch(
        change_id,
        repo_root=tmp_path,
        stage="APPLY_RED",
        slice_id=slice_id,
        hop_status="BLOCKED",
        hop_answer={
            "blockers": [{"code": "SCAFFOLD_AUTHORITY_MISSING", "detail": "test"}],
        },
    )
    assert red.ok, red.code
    assert red.path is not None
    red_proc = _run_schema(_RED_VALIDATOR, _SCHEMA_RED, red.path)
    assert red_proc.returncode == 0, red_proc.stdout + red_proc.stderr
    red_body = json.loads(red.path.read_text(encoding="utf-8"))
    assert red_body["apply_design_identity"]["planning_digest"] == "a" * 64


@pytest.mark.unit
def test_scaffold_dispatch_success_without_model_paths(tmp_path: Path) -> None:
    """Scaffold READY из dispatch: без created_paths от модели."""
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    work = "src/pkg/widget.py"
    product = tmp_path / work
    _ = product.write_text(
        "def widget():\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_SCAFFOLD",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
        },
    )
    assert result.ok, result.code
    assert result.path is not None
    proc = _run_schema(_SCAFFOLD_VALIDATOR, _SCHEMA_SCAFFOLD, result.path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "SCAFFOLD_READY"


@pytest.mark.unit
def test_scaffold_no_op_when_shape_operative_despite_no_op_allowed_false(
    tmp_path: Path,
) -> None:
    """no_op_allowed:false не блокирует runtime no-op при operative shape."""
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    design_path = tmp_path / "openspec" / "changes" / change_id / "apply-design" / "APPLY-DESIGN.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["slices"][0]["scaffold_contract"]["no_op_allowed"] = False
    _ = design_path.write_text(json.dumps(design, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "no-op-flag"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    work = "src/pkg/widget.py"
    _ = (tmp_path / work).write_text("def widget():\n    pass\n", encoding="utf-8")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_SCAFFOLD",
            "slice_id": slice_id,
            "hop_status": "NO_CHANGE",
            "hop_worktree_baseline": baseline,
        },
    )
    assert result.ok, result.code
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "SCAFFOLD_READY"
    assert body.get("no_op") is True


@pytest.mark.unit
def test_red_first_run_rejects_invalid_candidate_without_overwrite(tmp_path: Path) -> None:
    """Плохой RED hop не затирает принятый RED.json."""
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    red_dir.mkdir(parents=True, exist_ok=True)
    accepted = {
        "schema": "opsx.apply_red",
        "schema_version": 2,
        "evidence_contract": "per_command_v1",
        "change_id": change_id,
        "slice_id": slice_id,
        "status": "BLOCKED",
        "chat_history_required": False,
        "product_files_modified": False,
        "blockers": [{"code": "KEEP", "detail": "accepted"}],
        "next_stage": "NONE",
    }
    red_path = red_dir / "RED.json"
    _ = red_path.write_text(json.dumps(accepted, indent=2), encoding="utf-8")
    before = red_path.read_text(encoding="utf-8")
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_RED",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
        },
    )
    assert result.ok is False
    assert result.code and "RED" in result.code
    assert red_path.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_implement_rejects_empty_product_delta(tmp_path: Path) -> None:
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    red_dir.mkdir(parents=True, exist_ok=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_scaffold",
                "schema_version": 1,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "SCAFFOLD_READY",
                "chat_history_required": False,
                "next_stage": "APPLY_RED",
            },
        ),
        encoding="utf-8",
    )
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_red",
                "schema_version": 2,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "RED_PROVEN",
                "chat_history_required": False,
            },
        ),
        encoding="utf-8",
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "NO_CHANGE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [],
        },
    )
    assert result.ok is False
    assert result.code == "IMPLEMENT_NO_PRODUCT_DELTA"


@pytest.mark.unit
def test_implement_already_satisfied_no_product_delta_blocked_adoption_gap(
    tmp_path: Path,
) -> None:
    """ALREADY_SATISFIED + пустой product delta → BLOCKED adoption gap, не hard materialize fail."""
    change_id = "visual-adopt-hop"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    implement_dir = tmp_path / "openspec" / "changes" / change_id / "apply-implement" / slice_id
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    red_dir.mkdir(parents=True, exist_ok=True)
    implement_dir.mkdir(parents=True, exist_ok=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_scaffold",
                "schema_version": 1,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "SCAFFOLD_READY",
                "chat_history_required": False,
                "next_stage": "APPLY_RED",
            },
        ),
        encoding="utf-8",
    )
    test_rel = "tests/unit/pkg/test_widget_adopt.py"
    test_file = tmp_path / test_rel
    test_file.parent.mkdir(parents=True, exist_ok=True)
    _ = test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    test_cmd = f"poetry run pytest -q {test_rel}::test_ok"
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_red",
                "schema_version": 2,
                "evidence_contract": "per_command_v1",
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "ALREADY_SATISFIED",
                "observation_only": True,
                "implementation_write_authorized": False,
                "adoption_audit_eligible": True,
                "generated_at": "2026-01-01T00:00:00.000Z",
                "chat_history_required": False,
                "product_files_modified": False,
                "tests_frozen": True,
                "next_stage": "APPLY_IMPLEMENT",
                "test_paths": [test_rel],
                "fixture_paths": [],
                "test_commands": [test_cmd],
                "command_executions": [
                    {
                        "command": test_cmd,
                        "final_exit_code": 0,
                        "final_classification": "ALREADY_SATISFIED",
                        "red_qualified": False,
                        "pass_qualified": True,
                        "attempts": [
                            {
                                "attempt": 1,
                                "exit_code": 0,
                                "failure_classification": "ALREADY_SATISFIED",
                            },
                        ],
                    },
                ],
                "test_content_identity": "0" * 64,
                "blockers": [],
            },
        ),
        encoding="utf-8",
    )
    product = tmp_path / "src" / "pkg" / "widget.py"
    if product.is_file():
        product.unlink()
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fixtures"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "NO_CHANGE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [],
        },
    )
    assert result.ok is True, result.code
    assert result.path is not None
    assert result.path.is_file()
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "BLOCKED"
    codes = {str(b.get("code") or "") for b in body.get("blockers") or []}
    assert codes & {
        "ADOPTION_EVIDENCE_GAP",
        "ADOPTION_AUDIT_MISSING",
        "IMPLEMENTATION_AUTHORITY_MISSING",
        "ADOPTION_PRODUCT_MISSING",
    }
    proc = _run_schema(_IMPLEMENT_VALIDATOR, _SCHEMA_IMPLEMENT, result.path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.unit
def test_implement_already_satisfied_no_product_delta_adoption_adopted(
    tmp_path: Path,
) -> None:
    """ALREADY_SATISFIED + пустой product delta → pipeline adoption → IMPLEMENTATION_ADOPTED."""
    change_id = "visual-adopt-hop"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    red_dir.mkdir(parents=True, exist_ok=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_scaffold",
                "schema_version": 1,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "SCAFFOLD_READY",
                "chat_history_required": False,
                "next_stage": "APPLY_RED",
            },
        ),
        encoding="utf-8",
    )
    test_rel = "tests/unit/pkg/test_widget_adopt.py"
    test_file = tmp_path / test_rel
    test_file.parent.mkdir(parents=True, exist_ok=True)
    _ = test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    test_cmd = f"poetry run pytest -q {test_rel}::test_ok"
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_red",
                "schema_version": 2,
                "evidence_contract": "per_command_v1",
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "ALREADY_SATISFIED",
                "observation_only": True,
                "implementation_write_authorized": False,
                "adoption_audit_eligible": True,
                "generated_at": "2026-01-01T00:00:00.000Z",
                "chat_history_required": False,
                "product_files_modified": False,
                "tests_frozen": True,
                "next_stage": "APPLY_IMPLEMENT",
                "test_paths": [test_rel],
                "fixture_paths": [],
                "test_commands": [test_cmd],
                "command_executions": [
                    {
                        "command": test_cmd,
                        "final_exit_code": 0,
                        "final_classification": "ALREADY_SATISFIED",
                        "red_qualified": False,
                        "pass_qualified": True,
                        "attempts": [
                            {
                                "attempt": 1,
                                "exit_code": 0,
                                "failure_classification": "ALREADY_SATISFIED",
                            },
                        ],
                    },
                ],
                "test_content_identity": "0" * 64,
                "blockers": [],
            },
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fixtures"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "NO_CHANGE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [],
        },
    )
    assert result.ok is True, result.code
    assert result.path is not None
    assert result.path.is_file()
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "IMPLEMENTATION_ADOPTED"
    assert body.get("branch") == "ADOPTION"
    red_body = json.loads((red_dir / "RED.json").read_text(encoding="utf-8"))
    assert body.get("frozen_test_content_identity") == red_body.get("test_content_identity")
    assert body.get("current_byte_adoption_authority", {}).get("candidate_digest")
    attestation = (
        tmp_path
        / "opsx"
        / "receipts"
        / change_id
        / "adoption"
        / slice_id
        / "pipeline-owner-attestation.json"
    )
    assert attestation.is_file()
    proc = _run_schema(
        _IMPLEMENT_VALIDATOR,
        _SCHEMA_IMPLEMENT,
        result.path,
        repo_root=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    validator_out = json.loads(proc.stdout)
    assert validator_out.get("transition_authorized") is True


@pytest.mark.unit
def test_commit_receipt_invalid_candidate_preserves_prior_implement(
    tmp_path: Path,
) -> None:
    """Атомарный publish: невалидный candidate не портит последний принятый receipt."""
    change_id = "atomic-implement"
    slice_id = "SLICE-001"
    _init_repo(tmp_path)
    _write_minimal_design(tmp_path, change_id, slice_id)
    good = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "change_id": change_id,
        "slice_id": slice_id,
        "status": "BLOCKED",
        "generated_at": "2026-01-01T00:00:00Z",
        "chat_history_required": False,
        "apply_design_identity": {
            "machine_path": f"openspec/changes/{change_id}/apply-design/APPLY-DESIGN.json",
            "status": "APPLY_DESIGN_READY",
            "planning_digest": "a" * 64,
            "inspected_digest": "b" * 64,
            "protected_context_digest": "c" * 64,
            "artifact_sha256": "d" * 64,
        },
        "blockers": [{"code": "HOP_BLOCKED", "detail": "prior"}],
        "next_stage": "NONE",
        "product_files_modified": False,
        "tests_modified": False,
        "tests_executed": False,
    }
    impl_dir = tmp_path / "openspec" / "changes" / change_id / "apply-implement" / slice_id
    impl_dir.mkdir(parents=True, exist_ok=True)
    impl_path = impl_dir / "IMPLEMENTATION.json"
    prior_text = json.dumps(good, indent=2) + "\n"
    _ = impl_path.write_text(prior_text, encoding="utf-8")
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    red_dir.mkdir(parents=True, exist_ok=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_scaffold",
                "schema_version": 1,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "SCAFFOLD_READY",
                "chat_history_required": False,
                "next_stage": "APPLY_RED",
            },
        ),
        encoding="utf-8",
    )
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_red",
                "schema_version": 2,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "RED_PROVEN",
                "chat_history_required": False,
            },
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "receipts"], cwd=tmp_path, check=True, capture_output=True)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    work = "src/pkg/widget.py"
    (tmp_path / work).write_text("def widget():\n    return 1\n", encoding="utf-8")
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [work],
        },
    )
    assert result.ok is False
    assert impl_path.read_text(encoding="utf-8") == prior_text


@pytest.mark.unit
def test_record_hop_decision_cli_stdin_is_utf8_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: stdin к Node должен быть UTF-8 bytes, не cp1251 text."""
    _init_repo(tmp_path)
    captured: dict[str, object] = {}

    def _fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"ok":false,"code":"SLICE_NOT_IN_DESIGN"}\n',
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    arrow = "\u2192"
    _ = StageReceiptWriter.record_hop_decision(
        "hop-utf8",
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_SCAFFOLD",
            "slice_id": "SLICE-001",
            "hop_status": "BLOCKED",
            "blockers": [{"code": "NOTE", "detail": f"scope {arrow} next"}],
        },
    )
    raw = captured.get("input")
    assert isinstance(raw, (bytes, bytearray))
    assert arrow.encode("utf-8") in bytes(raw)


@pytest.mark.unit
def test_red_materialize_visual_slice002_pass_qualified_not_met_markers() -> None:
    """Live pass + NOT-MET → RED_ORACLE_BEHAVIOR_MARKER_GAP, receipt не публикуется."""
    change_id = "visual"
    slice_id = "SLICE-002"
    scaffold_path = (
        _REPO_ROOT
        / "openspec"
        / "changes"
        / change_id
        / "apply-scaffold"
        / slice_id
        / "SCAFFOLD.json"
    )
    if not scaffold_path.is_file():
        pytest.skip("visual SLICE-002 scaffold missing on disk")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=_REPO_ROOT,
        decision={
            "stage": "APPLY_RED",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [],
        },
    )
    assert result.ok is False
    assert result.code == "RED_ORACLE_BEHAVIOR_MARKER_GAP"
    red_path = (
        _REPO_ROOT
        / "openspec"
        / "changes"
        / change_id
        / "apply-red"
        / slice_id
        / "RED.json"
    )
    if red_path.is_file():
        body = json.loads(red_path.read_text(encoding="utf-8"))
        assert body.get("status") != "ALREADY_SATISFIED"


@pytest.mark.unit
def test_composition_root_imports() -> None:
    """Composition root: run_verify_impl + apply_scaffold prompt (no import cycle)."""
    import run_verify_impl  # noqa: F401, PLC0415
    from agents.implementation.apply_scaffold.prompt.get_prompt import (  # noqa: PLC0415
        get_prompt as scaffold_prompt,
    )

    _ = scaffold_prompt(
        repository_path=str(_REPO_ROOT),
        change_id="visual",
        impl_verify_answer={"slice_id": "SLICE-001"},
        artifact_bundle={},
        machine_context={},
    )
