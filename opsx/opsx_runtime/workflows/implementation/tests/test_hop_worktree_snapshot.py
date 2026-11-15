"""Снимок hop worktree: pre-hop bytes, untracked, модель не доказательство."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from workflows.implementation.hop_worktree import (
    HOP_WORKTREE_SNAPSHOT_SCHEMA,
    capture_hop_worktree_baseline,
    hop_snapshot_changed_paths,
    parse_hop_worktree_snapshot,
)


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    _ = (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp_path, check=True, capture_output=True)


@pytest.mark.unit
def test_preexisting_file_not_attributed_to_hop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    product = tmp_path / "src" / "product.mjs"
    product.parent.mkdir(parents=True, exist_ok=True)
    _ = product.write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "product"], cwd=tmp_path, check=True, capture_output=True)
    baseline = capture_hop_worktree_baseline(tmp_path, ["src/product.mjs"])
    parsed = parse_hop_worktree_snapshot(baseline)
    assert parsed is not None
    assert parsed["schema"] == HOP_WORKTREE_SNAPSHOT_SCHEMA
    changed = hop_snapshot_changed_paths(tmp_path, baseline, candidate_paths=["src/product.mjs"])
    assert changed == []


@pytest.mark.unit
def test_untracked_hop_file_detected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    baseline = capture_hop_worktree_baseline(tmp_path, [])
    new_file = tmp_path / "src" / "new.mjs"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    _ = new_file.write_text("hop", encoding="utf-8")
    changed = hop_snapshot_changed_paths(
        tmp_path,
        baseline,
        candidate_paths=["src/new.mjs"],
    )
    assert "src/new.mjs" in changed


@pytest.mark.unit
def test_model_claim_without_write_not_counted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    baseline = capture_hop_worktree_baseline(tmp_path, ["src/phantom.mjs"])
    changed = hop_snapshot_changed_paths(
        tmp_path,
        baseline,
        candidate_paths=["src/phantom.mjs"],
    )
    assert changed == []


@pytest.mark.unit
def test_snapshot_json_roundtrip_has_files_digest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    path = tmp_path / "w.txt"
    _ = path.write_text("x", encoding="utf-8")
    raw = capture_hop_worktree_baseline(tmp_path, ["w.txt"])
    body = json.loads(raw)
    assert "files" in body
    assert body["files"]["w.txt"]
