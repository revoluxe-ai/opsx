"""Receipt helpers для implementation lane (parity с apply pipeline на диске)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents.verify.impl_verify.preflight import change_root
from check_workflow_receipt import CheckWorkflowReceipt
from workflows.artifact_paths import (
    apply_design_artifact_path,
    apply_design_review_receipt_path,
)

APPLY_DESIGN_REVIEW_PASS = "APPLY_DESIGN_REVIEW_PASS"


def apply_design_review_path(change_id: str, *, repo_root: Path) -> Path:
    """``apply-design-review/APPLY-DESIGN-REVIEW.json`` в ``openspec/changes``."""
    return apply_design_review_receipt_path(repo_root, change_id)


def load_apply_design_review(
    change_id: str,
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    """Загрузить design-review receipt."""
    path = apply_design_review_path(change_id, repo_root=repo_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def is_apply_design_review_pass(
    change_id: str,
    *,
    repo_root: Path,
) -> bool:
    """True если review receipt в статусе ``APPLY_DESIGN_REVIEW_PASS``."""
    review = load_apply_design_review(change_id, repo_root=repo_root)
    if review is None:
        return False
    return str(review.get("status") or "") == APPLY_DESIGN_REVIEW_PASS


def apply_design_file_sha256(change_id: str, *, repo_root: Path) -> str | None:
    """SHA-256 (hex) сырых байтов ``APPLY-DESIGN.json`` — parity с Node ``fileSha256``."""
    path = apply_design_path(change_id, repo_root=repo_root)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_apply_design_review_fresh(
    change_id: str,
    *,
    repo_root: Path,
) -> bool:
    """Review PASS свежий, если digest файла совпадает с ``review.apply_design_identity.artifact_sha256``."""
    if not is_apply_design_review_pass(change_id, repo_root=repo_root):
        return False
    review = load_apply_design_review(change_id, repo_root=repo_root)
    if review is None:
        return False
    d_sha = apply_design_file_sha256(change_id, repo_root=repo_root)
    r_sha = (review.get("apply_design_identity") or {}).get("artifact_sha256")
    if not d_sha or not r_sha:
        return False
    if str(d_sha) == str(r_sha):
        return True
    path = apply_design_path(change_id, repo_root=repo_root)
    return bool(r_sha) and path.is_file() and CheckWorkflowReceipt.matches_file(path, str(r_sha))


def apply_design_path(change_id: str, *, repo_root: Path) -> Path:
    """Канонический ``APPLY-DESIGN.json`` в ``openspec/changes``."""
    return apply_design_artifact_path(repo_root, change_id)


def load_apply_design(change_id: str, *, repo_root: Path) -> dict[str, Any] | None:
    """Загрузить frozen APPLY-DESIGN.json."""
    path = apply_design_path(change_id, repo_root=repo_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None
