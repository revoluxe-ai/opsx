"""Runtime-встройка slice/stage receipts в split-apply prompt."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.implementation.prompt.scaffold_receipt_legal_shape import (
    scaffold_receipt_legal_shape_embed,
)
from workflows.implementation.receipt_loader import (
    load_apply_design,
    load_apply_design_review,
)
from workflows.implementation.slice_receipt import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    load_stage_receipt,
)


def build_stage_slice_embed(
    *,
    stage_key: str,
    change_id: str,
    slice_id: str | None,
    repo_root: Path,
) -> dict[str, Any]:
    """Полные receipts текущего hop (runtime read, не модель). Без обрезки."""
    embed: dict[str, Any] = {"stage_key": stage_key, "slice_id": slice_id}
    if stage_key in ("apply_scaffold", "apply_red", "apply_implement", "apply_green"):
        embed["slice_receipt_runtime_owned"] = True
    if stage_key == "apply_scaffold":
        embed["scaffold_json_field_lists"] = scaffold_receipt_legal_shape_embed()
    design = load_apply_design(change_id, repo_root=repo_root)
    if design is not None:
        embed["apply_design_json"] = json.dumps(design, ensure_ascii=False)
    review = load_apply_design_review(change_id, repo_root=repo_root)
    if review is not None:
        embed["apply_design_review_json"] = json.dumps(review, ensure_ascii=False)
    if not slice_id:
        return embed
    for stage, key in (
        (STAGE_APPLY_SCAFFOLD, "scaffold_json"),
        (STAGE_APPLY_RED, "red_json"),
        (STAGE_APPLY_IMPLEMENT, "implement_json"),
        (STAGE_APPLY_GREEN, "green_json"),
    ):
        data = load_stage_receipt(change_id, slice_id, stage, repo_root=repo_root)
        if data is not None:
            embed[key] = json.dumps(data, ensure_ascii=False)
    return embed

