"""Регрессия: гибридный SCAFFOLD.json как в visual d168017caeab должен FAIL schema."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_VALIDATOR = _REPO_ROOT / "opsx_tools" / "opsx-apply-scaffold.mjs"
_SCHEMA = _REPO_ROOT / "opsx_tools" / "registry" / "apply_scaffold.schema.json"

# Форма, которую первый hop run-visual-10 записал на диск перед oneOf_mismatch.
_VISUAL_HYBRID_RECEIPT = {
    "schema": "opsx.apply_scaffold",
    "schema_version": 1,
    "change_id": "visual",
    "slice_id": "SLICE-005",
    "status": "SCAFFOLD_READY",
    "generated_at": "2026-11-12T06:00:00.000Z",
    "chat_history_required": False,
    "no_op": True,
    "authorized_write_paths": [
        "src/stroy/consultation/invest_yalta/handler.py",
        "src/stroy/channels/max/invest_yalta/max_present.py",
    ],
    "behavior_implemented": False,
    "tests_modified": False,
    "tests_executed": False,
    "next_stage": "APPLY_RED",
    "blockers": [],
}


def _run_schema_validator(
    receipt: dict[str, object],
    *,
    receipt_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Запустить opsx-apply-scaffold.mjs в режиме чистой JSON Schema проверки."""
    assert _VALIDATOR.is_file(), _VALIDATOR
    assert _SCHEMA.is_file(), _SCHEMA
    _ = receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return subprocess.run(
        ["node", str(_VALIDATOR), "--schema", str(_SCHEMA), str(receipt_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.unit
def test_visual_hybrid_scaffold_receipt_fails_one_of(tmp_path: Path) -> None:
    """Частичный SCAFFOLD_READY + design keys без ready branch остаётся невалидным."""
    proc = _run_schema_validator(_VISUAL_HYBRID_RECEIPT, receipt_path=tmp_path / "SCAFFOLD.json")
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0
    assert "oneOf_mismatch" in combined or "oneOf" in combined.lower()


@pytest.mark.unit
def test_blocked_branch_rejects_scaffold_ready_status(tmp_path: Path) -> None:
    """SCAFFOLD_READY с полями blocked-ветки не должна проходить как BLOCKED."""
    proc = _run_schema_validator(_VISUAL_HYBRID_RECEIPT, receipt_path=tmp_path / "SCAFFOLD.json")
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert 'must be "BLOCKED"' in combined or "BLOCKED" in combined


@pytest.mark.unit
def test_visual_hybrid_with_work_scope_digest_still_fails_one_of(tmp_path: Path) -> None:
    """POST-hop digest не спасает гибрид run-visual-12 hop-3 (lane и edit guard)."""
    body = {**_VISUAL_HYBRID_RECEIPT, "work_scope_digest": "a" * 64}
    proc = _run_schema_validator(body, receipt_path=tmp_path / "SCAFFOLD.json")
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0
    assert "oneOf_mismatch" in combined or "oneof" in combined.lower()


@pytest.mark.unit
def test_visual_slice005_hop3_shape_allows_work_scope_digest(tmp_path: Path) -> None:
    """Полная SCAFFOLD_READY ветка (hop 3) остаётся валидной с runtime digest."""
    fixture = _REPO_ROOT / (
        "openspec/changes/visual/apply-scaffold/SLICE-005/SCAFFOLD.json"
    )
    if not fixture.is_file():
        pytest.skip("visual SLICE-005 SCAFFOLD.json missing")
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["work_scope_digest"] = "f" * 64
    proc = _run_schema_validator(body, receipt_path=tmp_path / "SCAFFOLD.json")
    assert proc.returncode == 0, proc.stdout
