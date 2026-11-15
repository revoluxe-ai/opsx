"""Fixtures для agent_tools tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def minimal_design(tmp_path: Path) -> tuple[Path, str]:
    """Канонический APPLY-DESIGN с одной image_task."""
    change_id = "visual"
    root = tmp_path
    design_dir = root / "openspec" / "changes" / change_id / "apply-design"
    design_dir.mkdir(parents=True)
    payload = {
        "schema": "opsx.apply_design",
        "schema_version": 4,
        "change_id": change_id,
        "status": "APPLY_DESIGN_READY",
        "generated_at": "2026-01-01T00:00:00Z",
        "chat_history_required": False,
        "write_boundary": {
            "product_code": False,
            "tests": False,
            "allowed_paths": ["openspec/changes/visual/apply-design/APPLY-DESIGN.json"],
        },
        "planning_identity": {
            "change_root": f"openspec/changes/{change_id}",
            "inputs": [],
            "planning_digest": "0" * 64,
        },
        "artifact_identity": {
            "human_path": "openspec/changes/visual/apply-design/APPLY-DESIGN.json",
            "machine_path": "openspec/changes/visual/apply-design/APPLY-DESIGN.json",
        },
        "blockers": [],
        "next_stage": "apply_scaffold",
        "slices": [
            {
                "slice_id": "SLICE-001",
                "title": "covers",
                "scaffold_contract": {},
                "image_tasks": [
                    {
                        "task_id": "t1.cover",
                        "purpose": "test cover",
                        "output_path": "data/out.png",
                        "width": 108,
                        "height": 135,
                        "format": "png",
                        "copy_text": ["Hello"],
                        "style_refs": [],
                        "acceptance": {
                            "ihdr": [108, 135],
                            "registry_member": False,
                            "visual_rubric": "Readable title",
                        },
                        "generation_mode": "compose_only",
                    }
                ],
            }
        ],
    }
    design_dir.joinpath("APPLY-DESIGN.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    (root / "opsx").mkdir()
    return root, change_id
