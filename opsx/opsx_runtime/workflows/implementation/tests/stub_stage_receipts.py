"""Минимальные machine-receipt на диске для stub hop-агентов (POST gate slice_receipt)."""

from __future__ import annotations

import json
from pathlib import Path

from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)


def write_stub_stage_receipt(
    *,
    repo_root: Path,
    change_id: str,
    slice_id: str,
    stage: str,
) -> None:
    """Создать receipt тела (без fingerprints) — runtime допишет отпечатки после hop."""
    root = repo_root / "openspec" / "changes" / change_id
    if stage == STAGE_APPLY_SCAFFOLD:
        folder = root / "apply-scaffold" / slice_id
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"status": "SCAFFOLD_READY", "created_paths": [], "extended_paths": []}
        _ = (folder / "SCAFFOLD.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if stage == STAGE_APPLY_RED:
        rel = "tests/contract_stub_red.py"
        test_file = repo_root / rel
        test_file.parent.mkdir(parents=True, exist_ok=True)
        if not test_file.is_file():
            _ = test_file.write_text("def test_contract_stub(): pass\n", encoding="utf-8")
        folder = root / "apply-red" / slice_id
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"status": "RED_PROVEN", "test_paths": [rel], "fixture_paths": []}
        _ = (folder / "RED.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return
    if stage == STAGE_APPLY_IMPLEMENT:
        rel = "src/contract_stub_module.py"
        prod = repo_root / rel
        prod.parent.mkdir(parents=True, exist_ok=True)
        if not prod.is_file():
            _ = prod.write_text("VALUE = 1\n", encoding="utf-8")
        folder = root / "apply-implement" / slice_id
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "IMPLEMENTATION_WRITTEN",
            "production_paths_modified": [rel],
            "production_paths_created": [],
        }
        _ = (folder / "IMPLEMENTATION.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if stage == STAGE_APPLY_GREEN:
        folder = root / "apply-green" / slice_id
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"status": "GREEN_PROVEN"}
        _ = (folder / "GREEN.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
