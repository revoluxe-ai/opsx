"""POST-hop scaffold validator must load its schema from the OPSX core.

A consumer checkout sets OPSX_PROJECT_ROOT to the product tree, which does not
contain opsx_tools/registry. The operative CLI used to inherit that root and
fail with missing_path, so a schema-valid SCAFFOLD receipt became
RECEIPT_NOT_ACCEPTED after the hop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows.implementation.slice_stage_operative_validation import (
    validate_scaffold_operative,
)

_CORE_ROOT = Path(__file__).resolve().parents[5]
_PROJECT_ROOT = Path(__file__).resolve().parents[7]

_READY_RECEIPT = {
    "schema": "opsx.apply_scaffold",
    "schema_version": 1,
    "change_id": "example",
    "slice_id": "slice-a",
    "apply_design_identity": {
        "machine_path": "openspec/changes/example/apply-design/APPLY-DESIGN.json",
        "status": "APPLY_DESIGN_READY",
        "planning_digest": "a" * 64,
        "inspected_digest": "b" * 64,
        "protected_context_digest": "c" * 64,
        "artifact_sha256": "d" * 64,
    },
    "status": "SCAFFOLD_READY",
    "generated_at": "2026-11-17T00:00:00.000Z",
    "chat_history_required": False,
    "repository_identity_before": {
        "head": "abc123",
        "inspected_paths": ["src/mod.py"],
        "inspected_digest": "e" * 64,
    },
    "repository_identity_after": {
        "head": "abc123",
        "inspected_paths": ["src/mod.py"],
        "inspected_digest": "e" * 64,
    },
    "required_surfaces": [
        {
            "path": "src/mod.py",
            "action": "INTRODUCE",
            "required_symbols": ["Sym"],
        }
    ],
    "created_paths": ["src/mod.py"],
    "extended_paths": [],
    "created_symbols": [],
    "structural_changes": [
        {"path": "src/mod.py", "kind": "create", "detail": "shape"}
    ],
    "no_op": False,
    "behavior_implemented": False,
    "product_files_modified": False,
    "tests_modified": False,
    "tests_executed": False,
    "authorized_write_paths": ["src/mod.py"],
    "other_eligible_slice_ids": [],
    "blockers": [],
    "next_stage": "APPLY_RED",
}


@pytest.mark.unit
def test_scaffold_operative_uses_core_schema_when_project_has_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Схема scaffold берётся из OPSX_CORE_ROOT, даже если в проекте её нет."""
    project_schema = (
        _PROJECT_ROOT / "opsx_tools" / "registry" / "apply_scaffold.schema.json"
    )
    assert not project_schema.is_file()
    monkeypatch.setenv("OPSX_PROJECT_ROOT", str(_PROJECT_ROOT))
    monkeypatch.setenv("OPSX_CORE_ROOT", str(_CORE_ROOT))
    receipt_path = tmp_path / "SCAFFOLD.json"
    _ = receipt_path.write_text(
        json.dumps(_READY_RECEIPT),
        encoding="utf-8",
    )
    result = validate_scaffold_operative(receipt_path, repo_root=_PROJECT_ROOT)
    assert result.ok, result.errors
    assert result.status == "PASS"
