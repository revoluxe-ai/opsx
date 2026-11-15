"""Регрессия: гибридный SCAFFOLD.json отклоняется сразу после edit tool, не как success write."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agents.config import ReadScopeViolation
from agents.implementation.apply_scaffold.scaffold_receipt_write import (
    SCAFFOLD_RECEIPT_SCHEMA_RULE,
    enforce_scaffold_receipt_after_edit,
)
from agents.implementation.tests.test_apply_scaffold_receipt_machine_schema import (
    _VISUAL_HYBRID_RECEIPT,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_TEST_CHANGE = "_pytest_scaffold_write_guard"
_TEST_SLICE = "SLICE-001"


def _scaffold_dir() -> Path:
    return (
        _REPO_ROOT
        / "openspec"
        / "changes"
        / _TEST_CHANGE
        / "apply-scaffold"
        / _TEST_SLICE
    )


def _scaffold_relpath() -> str:
    return (
        f"openspec/changes/{_TEST_CHANGE}/apply-scaffold/{_TEST_SLICE}/SCAFFOLD.json"
    )


@pytest.fixture
def scaffold_receipt_path() -> Path:
    folder = _scaffold_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SCAFFOLD.json"
    yield path
    if folder.parent.parent.exists():
        shutil.rmtree(folder.parent.parent, ignore_errors=True)


_BLOCKED_RECEIPT = {
    "schema": "opsx.apply_scaffold",
    "schema_version": 1,
    "change_id": _TEST_CHANGE,
    "slice_id": _TEST_SLICE,
    "status": "BLOCKED",
    "generated_at": "2026-11-12T06:00:00.000Z",
    "chat_history_required": False,
    "behavior_implemented": False,
    "tests_modified": False,
    "tests_executed": False,
    "blockers": [{"code": "INPUT_SCOPE_INCOMPLETE", "detail": "test"}],
    "next_stage": "NONE",
}


@pytest.mark.unit
def test_hybrid_scaffold_edit_rejected_and_rolled_back(scaffold_receipt_path: Path) -> None:
    """Visual hybrid из run-visual-10/11 не должен оставаться на диске после edit."""
    prior = json.dumps({"status": "prior"}, indent=2)
    _ = scaffold_receipt_path.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": _scaffold_relpath()}
    started = {"kind": "interaction", "update_type": "tool-call-started"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        started,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    _ = scaffold_receipt_path.write_text(
        json.dumps(_VISUAL_HYBRID_RECEIPT),
        encoding="utf-8",
    )
    completed = {"kind": "interaction", "update_type": "tool-call-completed"}
    with pytest.raises(ReadScopeViolation) as err:
        enforce_scaffold_receipt_after_edit(
            "edit",
            args,
            completed,
            repo_root=_REPO_ROOT,
            snapshots=snapshots,
        )
    assert err.value.rule == SCAFFOLD_RECEIPT_SCHEMA_RULE
    combined = (err.value.command or "").lower()
    assert "oneof" in combined or "oneof_mismatch" in combined
    assert scaffold_receipt_path.read_text(encoding="utf-8") == prior


@pytest.mark.unit
def test_hybrid_with_digest_edit_guard_matches_lane_schema_fail(
    scaffold_receipt_path: Path,
) -> None:
    """Гибрид + work_scope_digest: edit guard отклоняет так же, как lane oneOf gate."""
    prior = json.dumps({"status": "prior"}, indent=2)
    _ = scaffold_receipt_path.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": _scaffold_relpath()}
    started = {"kind": "interaction", "update_type": "tool-call-started"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        started,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    hybrid = {**_VISUAL_HYBRID_RECEIPT, "work_scope_digest": "b" * 64}
    _ = scaffold_receipt_path.write_text(json.dumps(hybrid, indent=2), encoding="utf-8")
    completed = {"kind": "interaction", "update_type": "tool-call-completed"}
    with pytest.raises(ReadScopeViolation) as err:
        enforce_scaffold_receipt_after_edit(
            "edit",
            args,
            completed,
            repo_root=_REPO_ROOT,
            snapshots=snapshots,
        )
    assert err.value.rule == SCAFFOLD_RECEIPT_SCHEMA_RULE
    assert scaffold_receipt_path.read_text(encoding="utf-8") == prior


@pytest.mark.unit
def test_visual_slice005_hop3_receipt_passes_projected_edit_guard(
    scaffold_receipt_path: Path,
) -> None:
    """Hop 3 on-disk SCAFFOLD_READY проходит projected edit guard (run-visual-12)."""
    fixture = _REPO_ROOT / (
        "openspec/changes/visual/apply-scaffold/SLICE-005/SCAFFOLD.json"
    )
    if not fixture.is_file():
        pytest.skip("visual SLICE-005 SCAFFOLD.json missing")
    prior = json.dumps({"status": "prior"}, indent=2)
    _ = scaffold_receipt_path.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": _scaffold_relpath()}
    started = {"kind": "tool_call", "status": "running"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        started,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    hop3_body = fixture.read_text(encoding="utf-8")
    _ = scaffold_receipt_path.write_text(hop3_body, encoding="utf-8")
    completed = {"kind": "tool_call", "status": "completed"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        completed,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    assert json.loads(scaffold_receipt_path.read_text(encoding="utf-8")) == json.loads(
        hop3_body,
    )


@pytest.mark.unit
def test_blocked_scaffold_receipt_passes_edit_gate(scaffold_receipt_path: Path) -> None:
    snapshots: dict[str, str] = {}
    args = {"path": _scaffold_relpath()}
    _ = scaffold_receipt_path.write_text(json.dumps(_BLOCKED_RECEIPT), encoding="utf-8")
    completed = {"kind": "tool_call", "status": "completed"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        completed,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    loaded = json.loads(scaffold_receipt_path.read_text(encoding="utf-8"))
    assert loaded["status"] == "BLOCKED"
