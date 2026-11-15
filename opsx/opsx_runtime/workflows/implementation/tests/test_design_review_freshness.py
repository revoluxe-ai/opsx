"""Freshness: review привязан к sha256 байтов APPLY-DESIGN.json, не к полям внутри JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from workflows.implementation.receipt_loader import (
    APPLY_DESIGN_REVIEW_PASS,
    is_apply_design_review_fresh,
)


def _write_design_review_pair(
    tmp_path: Path,
    change_id: str,
    design_bytes: bytes,
    *,
    review_artifact_sha: str,
) -> None:
    change_dir = tmp_path / "openspec" / "changes" / change_id
    design_dir = change_dir / "apply-design"
    review_dir = change_dir / "apply-design-review"
    design_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    _ = (design_dir / "APPLY-DESIGN.json").write_bytes(design_bytes)
    review_doc = {
        "status": APPLY_DESIGN_REVIEW_PASS,
        "apply_design_identity": {"artifact_sha256": review_artifact_sha},
    }
    _ = (review_dir / "APPLY-DESIGN-REVIEW.json").write_text(
        json.dumps(review_doc, indent=2),
        encoding="utf-8",
    )


def test_fresh_when_file_hash_matches_review_without_design_identity_field(tmp_path: Path) -> None:
    """Опубликованный DESIGN без apply_design_identity; свежесть по байтам файла."""
    change_id = "fresh-bytes"
    design_doc = {"status": "APPLY_DESIGN_READY", "slice": "SLICE-001"}
    design_bytes = json.dumps(design_doc, indent=2).encode("utf-8")
    artifact_sha = hashlib.sha256(design_bytes).hexdigest()
    _write_design_review_pair(tmp_path, change_id, design_bytes, review_artifact_sha=artifact_sha)

    assert is_apply_design_review_fresh(change_id, repo_root=tmp_path) is True

    design_path = tmp_path / "openspec" / "changes" / change_id / "apply-design" / "APPLY-DESIGN.json"
    _ = design_path.write_bytes(design_bytes + b"\n")
    assert is_apply_design_review_fresh(change_id, repo_root=tmp_path) is False


def test_stale_strings_inside_json_do_not_replace_file_hash(tmp_path: Path) -> None:
    """Скопированные строки apply_design_identity в DESIGN не маскируют изменённые байты файла."""
    change_id = "stale-strings"
    design_doc = {"status": "APPLY_DESIGN_READY", "note": "v1"}
    design_bytes = json.dumps(design_doc, indent=2).encode("utf-8")
    artifact_sha = hashlib.sha256(design_bytes).hexdigest()
    _write_design_review_pair(tmp_path, change_id, design_bytes, review_artifact_sha=artifact_sha)

    design_path = tmp_path / "openspec" / "changes" / change_id / "apply-design" / "APPLY-DESIGN.json"
    tampered = {**design_doc, "note": "v2-mutated"}
    # В JSON подкладываем старый hash от исходных байтов — свежесть всё равно False.
    with_stale_identity = {**tampered, "apply_design_identity": {"artifact_sha256": artifact_sha}}
    _ = design_path.write_text(json.dumps(with_stale_identity, indent=2), encoding="utf-8")

    assert is_apply_design_review_fresh(change_id, repo_root=tmp_path) is False
