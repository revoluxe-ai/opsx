"""Снимок байтов hop-relevant файлов до SDK hop (не только git HEAD)."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.artifact_paths import apply_design_artifact_path
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.implementation.slice_receipt import load_stage_receipt, work_paths_from_receipt
from workflows.repo_paths import resolve_opsx_repository_root

HOP_WORKTREE_SNAPSHOT_SCHEMA = "hop_worktree_snapshot_v1"


def _resolve_worktree_root(candidate: Path) -> Path:
    """Корень для byte-snapshot: полный OPSX или произвольный git fixture в тестах."""
    root = candidate.resolve()
    if (resource_path(root, "opsx")).is_dir():
        return resolve_opsx_repository_root(root)
    return root


def _posix(path: str) -> str:
    return str(path).replace("\\", "/")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(repo_root: Path) -> str:
    run = run_subprocess_utf8(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        timeout=30,
    )
    if run.returncode != 0:
        return "unknown"
    head = (run.stdout or "").strip()
    return head or "unknown"


def collect_hop_watch_paths(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> list[str]:
    """Пути, чьи байты должны попасть в снимок hop (design/receipt/product/tests)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    paths: set[str] = set()
    design_path = apply_design_artifact_path(repo_root, change_id)
    if design_path.is_file():
        paths.add(_posix(design_path.relative_to(repo_root).as_posix()))
        try:
            design = json.loads(design_path.read_text(encoding="utf-8"))
            slices = design.get("slices") if isinstance(design, dict) else None
            if isinstance(slices, list):
                for row in slices:
                    if not isinstance(row, dict) or str(row.get("slice_id")) != slice_id:
                        continue
                    for item in row.get("expected_product_write_surfaces") or []:
                        if isinstance(item, str) and item.strip():
                            paths.add(_posix(item.strip()))
                    for item in row.get("red_test_targets_or_seams") or []:
                        if isinstance(item, str) and item.strip():
                            paths.add(_posix(item.strip()))
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    for hop_stage in (
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    ):
        if hop_stage != stage and hop_stage not in (
            STAGE_APPLY_SCAFFOLD,
            STAGE_APPLY_RED,
            STAGE_APPLY_IMPLEMENT,
        ):
            continue
        receipt = load_stage_receipt(change_id, slice_id, hop_stage, repo_root=repo_root)
        if not isinstance(receipt, dict):
            continue
        try:
            if hop_stage == STAGE_APPLY_GREEN:
                from workflows.implementation.slice_receipt import green_work_paths

                paths.update(green_work_paths(change_id, slice_id, repo_root=repo_root))
            else:
                paths.update(work_paths_from_receipt(hop_stage, receipt))
        except ValueError:
            pass
        for key in ("test_paths", "fixture_paths"):
            raw = receipt.get(key)
            if isinstance(raw, list):
                for p in raw:
                    if isinstance(p, str) and p.strip():
                        paths.add(_posix(p.strip()))
    listing = run_subprocess_utf8(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=repo_root,
        timeout=60,
    )
    if listing.returncode == 0:
        for line in (listing.stdout or "").splitlines():
            rel = _posix(line.strip())
            if not rel:
                continue
            if any(rel == w or rel.startswith(f"{w}/") for w in paths):
                paths.add(rel)
    return sorted(paths)


def capture_hop_worktree_baseline(
    repo_root: Path,
    watch_paths: list[str] | None = None,
) -> str:
    """Зафиксировать head и sha256 hop-relevant файлов (tracked + untracked на диске)."""
    repo_root = _resolve_worktree_root(repo_root)
    head = _git_head(repo_root)
    files: dict[str, str] = {}
    for raw in watch_paths or []:
        rel = _posix(str(raw).strip())
        if not rel:
            continue
        abs_path = resource_path(repo_root, rel)
        if abs_path.is_file():
            files[rel] = _sha256_file(abs_path)
    payload = {
        "schema": HOP_WORKTREE_SNAPSHOT_SCHEMA,
        "head": head,
        "files": files,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def parse_hop_worktree_snapshot(baseline: str) -> dict[str, Any] | None:
    """Разобрать JSON-снимок; legacy git sha возвращает None."""
    text = str(baseline or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema") != HOP_WORKTREE_SNAPSHOT_SCHEMA:
        return None
    return parsed


def git_diff_paths_since(repo_root: Path, head_ref: str) -> list[str]:
    """Имена путей из ``git diff --name-only`` относительно ``head_ref``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    ref = str(head_ref or "").strip()
    if not ref or ref == "unknown":
        return []
    run = run_subprocess_utf8(
        ["git", "diff", "--name-only", ref],
        cwd=repo_root,
        timeout=60,
    )
    if run.returncode != 0:
        return []
    return sorted(
        {_posix(line.strip()) for line in (run.stdout or "").splitlines() if line.strip()},
    )


def _expected_product_surfaces(
    repo_root: Path,
    change_id: str,
    slice_id: str,
) -> list[str]:
    """Поверхности продукта слайса из принятого design, без привязки к change."""
    design_path = apply_design_artifact_path(repo_root, change_id)
    if not design_path.is_file():
        return []
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    slices = design.get("slices") if isinstance(design, dict) else None
    if not isinstance(slices, list):
        return []
    surfaces: list[str] = []
    for row in slices:
        if not isinstance(row, dict) or str(row.get("slice_id") or "") != slice_id:
            continue
        raw = row.get("expected_product_write_surfaces")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item.strip():
                surfaces.append(_posix(item.strip()).rstrip("/"))
    return surfaces


def _files_under_surface(repo_root: Path, surface: str) -> list[str]:
    """Файл поверхности или файлы внутри каталога поверхности."""
    rel = _posix(surface).rstrip("/")
    if not rel or rel.startswith("../") or "/../" in f"/{rel}/":
        return []
    abs_path = resource_path(repo_root, rel)
    try:
        resolved = abs_path.resolve()
        resolved.relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return []
    if resolved.is_file():
        return [rel]
    if not resolved.is_dir():
        return []
    found: list[str] = []
    for path in resolved.rglob("*"):
        if not path.is_file():
            continue
        try:
            child = _posix(path.resolve().relative_to(repo_root.resolve()).as_posix())
        except ValueError:
            continue
        found.append(child)
    return found


def _off_head_product_file(repo_root: Path, rel: str) -> bool:
    """Файл есть на диске и его blob не совпадает с HEAD (untracked или dirty)."""
    abs_path = resource_path(repo_root, rel)
    if not abs_path.is_file():
        return False
    try:
        if abs_path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    work = run_subprocess_utf8(
        ["git", "hash-object", "--", rel],
        cwd=repo_root,
        timeout=30,
    )
    work_oid = (work.stdout or "").strip()
    if work.returncode != 0 or not work_oid:
        return False
    head = run_subprocess_utf8(
        ["git", "rev-parse", f"HEAD:{rel}"],
        cwd=repo_root,
        timeout=30,
    )
    if head.returncode != 0:
        return True
    return (head.stdout or "").strip() != work_oid


def augment_implement_decision_with_off_head_surfaces(
    repo_root: Path,
    change_id: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Сделать off-HEAD поверхности слайса видимыми для materialize IMPLEMENT.

    Снимок hop фиксирует байты до агента. Файл, созданный scaffold или прошлым
    hop и не изменённый в этом hop, не входит в delta, и CLI отвечает
    ``IMPLEMENT_NO_PRODUCT_DELTA``, хотя продукт уже лежит вне HEAD.
    Такие пути снимаются со снимка и добавляются в ``hop_files_changed``.
    Файл, совпадающий с HEAD, не добавляется: пустой hop по-прежнему отказ.
    """
    slice_id = str(decision.get("slice_id") or "").strip()
    if not slice_id:
        return decision
    promoted: list[str] = []
    seen: set[str] = set()
    for surface in _expected_product_surfaces(repo_root, change_id, slice_id):
        for rel in _files_under_surface(repo_root, surface):
            if rel in seen or not _off_head_product_file(repo_root, rel):
                continue
            seen.add(rel)
            promoted.append(rel)
    if not promoted:
        return decision
    updated = dict(decision)
    baseline = str(updated.get("hop_worktree_baseline") or "")
    snapshot = parse_hop_worktree_snapshot(baseline)
    if snapshot is not None:
        files = snapshot.get("files")
        if isinstance(files, dict):
            kept = {
                _posix(str(path)): sha
                for path, sha in files.items()
                if _posix(str(path)) not in seen
            }
            snapshot = {**snapshot, "files": kept}
            updated["hop_worktree_baseline"] = json.dumps(
                snapshot,
                separators=(",", ":"),
                sort_keys=True,
            )
    claimed_raw = updated.get("hop_files_changed")
    claimed: list[str] = []
    if isinstance(claimed_raw, list):
        claimed = [
            _posix(str(path))
            for path in claimed_raw
            if isinstance(path, str) and str(path).strip()
        ]
    for rel in promoted:
        if rel not in claimed:
            claimed.append(rel)
    updated["hop_files_changed"] = claimed
    return updated


def hop_snapshot_changed_paths(
    repo_root: Path,
    baseline: str,
    *,
    candidate_paths: list[str] | None = None,
) -> list[str]:
    """Пути, чьи байты изменились относительно снимка (модель files_changed не доказательство)."""
    repo_root = _resolve_worktree_root(repo_root)
    snapshot = parse_hop_worktree_snapshot(baseline)
    if snapshot is None:
        return []
    prior_files = snapshot.get("files")
    if not isinstance(prior_files, dict):
        prior_files = {}
    changed: set[str] = set()
    for rel, prior_sha in prior_files.items():
        path = _posix(str(rel))
        if not path:
            continue
        abs_path = resource_path(repo_root, path)
        if not abs_path.is_file():
            changed.add(path)
            continue
        if _sha256_file(abs_path) != str(prior_sha):
            changed.add(path)
    for raw in candidate_paths or []:
        rel = _posix(str(raw).strip())
        if not rel:
            continue
        abs_path = resource_path(repo_root, rel)
        if not abs_path.is_file():
            continue
        live = _sha256_file(abs_path)
        if rel not in prior_files or prior_files.get(rel) != live:
            changed.add(rel)
    return sorted(changed)
