"""Idempotency, repair revision, запись IMAGE-RESULT и receipt C."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Any

from agents.agent_tools.image.revision import output_file_revision
from agents.agent_tools.image.schemas import (
    ImageAssetTaskResult,
    ImageDesignTask,
    TechnicalGateVerdict,
    VisualReviewVerdict,
)


def image_result_path(change_id: str, task_id: str) -> str:
    return f"openspec/changes/{change_id}/apply-image/{task_id}/IMAGE-RESULT.json"


def image_receipt_path(change_id: str, task_id: str) -> str:
    return f"opsx/receipts/{change_id}/image-asset/{task_id}.receipt.json"


def load_prior_result(repo_root: Path, change_id: str, task_id: str) -> dict[str, Any] | None:
    path = resource_path(repo_root, image_result_path(change_id, task_id))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def prior_accepted_revision(repo_root: Path, change_id: str, task_id: str) -> str | None:
    prior = load_prior_result(repo_root, change_id, task_id)
    if not isinstance(prior, dict):
        return None
    if str(prior.get("status") or "") != "ACCEPTED":
        return None
    tech = prior.get("technical")
    if isinstance(tech, dict):
        rev = str(tech.get("output_revision") or "").strip()
        return rev or None
    return None


def repair_requires_new_bytes(
    prior_revision: str | None,
    current: TechnicalGateVerdict,
) -> bool:
    """True если repair без смены revision."""
    if not prior_revision:
        return False
    return prior_revision == current.output_revision


def publish_image_result(
    repo_root: Path,
    change_id: str,
    result: ImageAssetTaskResult,
) -> Path:
    rel = image_result_path(change_id, result.task_id)
    path = resource_path(repo_root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def publish_receipt(
    repo_root: Path,
    change_id: str,
    task: ImageDesignTask,
    *,
    technical: TechnicalGateVerdict,
    visual: VisualReviewVerdict,
) -> Path:
    rel = image_receipt_path(change_id, task.task_id)
    path = resource_path(repo_root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    out_path = (resource_path(repo_root, task.output_path)).resolve()
    payload = {
        "schema": "opsx.idempotency_receipt",
        "schema_version": 1,
        "node_id": "execute_image_task",
        "operation": "image_asset",
        "change_id": change_id,
        "slice_id": task.slice_id,
        "status": "PASS",
        "artifacts": [
            {
                "role": "image_output",
                "path": task.output_path.replace("\\", "/"),
                "sha256": technical.output_revision,
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _ = out_path
    return path


def idempotent_accepted(
    repo_root: Path,
    change_id: str,
    task: ImageDesignTask,
) -> ImageAssetTaskResult | None:
    """Reuse если ACCEPTED и байты не изменились."""
    prior = load_prior_result(repo_root, change_id, task.task_id)
    if not prior or str(prior.get("status")) != "ACCEPTED":
        return None
    out = resource_path(repo_root, task.output_path)
    if not out.is_file():
        return None
    rev = output_file_revision(out)
    prior_rev = prior_accepted_revision(repo_root, change_id, task.task_id)
    if prior_rev and prior_rev == rev:
        return ImageAssetTaskResult.model_validate(prior)
    return None
