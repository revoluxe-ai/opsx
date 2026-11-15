"""S3.5 worktree path → work-item reconciliation and per-WI ownership receipts."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .hermetic_boundary import hermetic_execution_enabled

RECEIPTS_DIR = Path("opsx/runs/orch-missions")
EXCLUDED_PREFIXES = (
    ".cursor/",
    ".tmp/",
    "openspec/",
    "opsx/",
    ".agents/",
    "tools/temporal/",
)

LOC_LINK_RE = re.compile(r"^LOC_LINK:\s*(.+)$", re.I | re.M)


def _normalize_path(raw: str) -> str:
    """Нормализовать путь; не снимать ведущую точку у ``opsx/**`` (git exclude)."""
    text = raw.replace("\\", "/").strip()
    if text.startswith("./"):
        return text[2:]
    return text


def _work_item_scope_paths(item: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    location = str(item.get("location") or "").strip()
    if location:
        paths.append(_normalize_path(location))
    loc_link = str(item.get("loc_link") or "").strip()
    if loc_link:
        paths.append(_normalize_path(loc_link))
    member = str(item.get("member_id") or "").strip()
    if member and location:
        paths.append(_normalize_path(location))
    return list(dict.fromkeys(paths))


def _paths_from_delta_map(
    delta_map: dict[str, Any],
    work_items: list[dict[str, Any]],
) -> dict[str, str]:
    """Map normalized path → work_item_id using explicit WORKTREE_DELTA_MAP."""

    wi_by_id = {str(w.get("work_item_id") or w.get("finding_id")): w for w in work_items}
    path_to_wi: dict[str, str] = {}
    for raw_path, meta in delta_map.items():
        path = _normalize_path(str(raw_path))
        if not path:
            continue
        wi_id = ""
        if isinstance(meta, dict):
            wi_id = str(meta.get("work_item_id") or "")
        elif isinstance(meta, str):
            wi_id = meta
        if not wi_id:
            continue
        if wi_id not in wi_by_id and not any(str(w.get("work_item_id")) == wi_id for w in work_items):
            continue
        if path in path_to_wi and path_to_wi[path] != wi_id:
            raise ValueError("ambiguous_path_ownership")
        path_to_wi[path] = wi_id
    return path_to_wi


def collect_git_dirty_paths(repo_root: Path) -> set[str]:
    """Собрать нормализованные product-lane пути из ``git status --porcelain``."""

    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    dirty: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = _normalize_path(line[3:].strip())
        if any(path.startswith(p) for p in EXCLUDED_PREFIXES):
            continue
        dirty.add(path)
    return dirty


def _validate_git_and_identity(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    path_to_wi: dict[str, str],
    delta_map: dict[str, Any] | None,
    dirty_paths: list[str] | None,
) -> dict[str, Any] | None:
    """Сверка delta/dirty с git и execution_id; при расхождении — fail closed."""

    summary_path = repo_root / RECEIPTS_DIR / mission_id / "worktree-ownership.json"
    if summary_path.is_file():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = None
        if isinstance(prior, dict):
            prior_exec = str(prior.get("execution_id") or "")
            if prior_exec and execution_id and prior_exec != execution_id:
                return {
                    "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                    "REASON": "stale_worktree_execution_identity",
                }

    if hermetic_execution_enabled(repo_root):
        return None

    git_dirty = collect_git_dirty_paths(repo_root)
    if not git_dirty and not path_to_wi:
        return None

    for path in git_dirty:
        if path not in path_to_wi:
            return {
                "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                "REASON": "unmapped_git_dirty_path",
                "PATH": path,
            }

    if dirty_paths is not None:
        requested = {_normalize_path(p) for p in dirty_paths if str(p).strip()}
        for path in requested:
            if path not in git_dirty and path not in path_to_wi:
                return {
                    "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                    "REASON": "stale_or_missing_dirty_path",
                    "PATH": path,
                }

    if delta_map:
        for raw_path in delta_map:
            path = _normalize_path(str(raw_path))
            if path and path not in git_dirty and path not in path_to_wi:
                return {
                    "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                    "REASON": "delta_map_not_in_git_worktree",
                    "PATH": path,
                }
    return None


def _receipt_path(repo_root: Path, mission_id: str, work_item_id: str) -> Path:
    return (
        repo_root
        / RECEIPTS_DIR
        / mission_id
        / "receipts"
        / f"worktree-ownership-{work_item_id}.json"
    )


def reconcile_worktree_ownership(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    work_items: list[dict[str, Any]],
    delta_map: dict[str, Any] | None = None,
    dirty_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Построить WORKTREE_DELTA_MAP и записать receipts/worktree-ownership-<WORK_ITEM_ID>.json.

    При неоднозначном владении путём возвращает BLOCKED_WORKTREE_OWNERSHIP.
    """

    repair_items = [
        w
        for w in work_items
        if w.get("route") in {"MUST_FIX_REPAIR", "MIXED_SPLIT"}
        or w.get("s4_repair_required")
    ]
    if not repair_items:
        return {
            "STATUS": "WORKTREE_OWNERSHIP_SKIPPED",
            "REASON": "no_s4_repair_items",
            "RECEIPTS": [],
        }

    explicit = delta_map or {}
    path_to_wi: dict[str, str] = {}
    if explicit:
        try:
            path_to_wi = _paths_from_delta_map(explicit, work_items)
        except ValueError:
            return {
                "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                "REASON": "ambiguous_path_ownership",
            }

    if not path_to_wi and dirty_paths:
        for raw in dirty_paths:
            path = _normalize_path(raw)
            if any(path.startswith(p) for p in EXCLUDED_PREFIXES):
                continue
            owners = []
            for item in repair_items:
                scopes = _work_item_scope_paths(item)
                if path in scopes or any(path.endswith(s) or s.endswith(path) for s in scopes):
                    owners.append(str(item.get("work_item_id") or item["finding_id"]))
            if len(owners) > 1:
                return {
                    "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                    "REASON": "ambiguous_path_ownership",
                    "PATH": path,
                    "OWNERS": owners,
                }
            if len(owners) == 1:
                path_to_wi[path] = owners[0]

    if not path_to_wi:
        for item in repair_items:
            wi_id = str(item.get("work_item_id") or item["finding_id"])
            for scope in _work_item_scope_paths(item):
                if scope in path_to_wi and path_to_wi[scope] != wi_id:
                    return {
                        "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                        "REASON": "ambiguous_path_ownership",
                    }
                path_to_wi[scope] = wi_id

    receipts: list[str] = []
    wi_paths: dict[str, list[str]] = {str(w.get("work_item_id") or w["finding_id"]): [] for w in repair_items}
    forbidden_by_wi: dict[str, list[str]] = {k: [] for k in wi_paths}

    for path, wi_id in path_to_wi.items():
        if wi_id in wi_paths:
            wi_paths[wi_id].append(path)

    identity_block = _validate_git_and_identity(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        path_to_wi=path_to_wi,
        delta_map=explicit if explicit else None,
        dirty_paths=dirty_paths,
    )
    if identity_block is not None:
        return identity_block

    all_paths = set(path_to_wi.keys())
    for wi_id, scope_paths in wi_paths.items():
        forbidden_by_wi[wi_id] = sorted(all_paths - set(scope_paths))

    for item in repair_items:
        wi_id = str(item.get("work_item_id") or item["finding_id"])
        scope = sorted(wi_paths.get(wi_id, _work_item_scope_paths(item)))
        if not scope:
            return {
                "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                "REASON": "missing_scope_for_repair_item",
                "WORK_ITEM_ID": wi_id,
            }
        payload = {
            "schema_version": "opsx-worktree-ownership-v1",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "work_item_id": wi_id,
            "finding_id": item.get("finding_id"),
            "WORK_ITEM_SCOPE_PATHS": scope,
            "PREEXISTING_PARTIAL_REPAIR_PATHS": scope,
            "FORBIDDEN_PATHS": forbidden_by_wi.get(wi_id, []),
            "WORKTREE_DELTA_MAP": {
                p: {"work_item_id": wi_id, "provenance": "S3_5_RECONCILE"}
                for p in scope
            },
        }
        path = _receipt_path(repo_root, mission_id, wi_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        receipts.append(str(path.relative_to(repo_root)))

    mission_summary = repo_root / RECEIPTS_DIR / mission_id / "worktree-ownership.json"
    mission_summary.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "mission_id": mission_id,
        "execution_id": execution_id,
        "ownership": "MISSION_WORKTREE_BOUND",
        "worktree_ownership_receipts": receipts,
        "WORKTREE_DELTA_MAP": path_to_wi,
    }
    tmp = mission_summary.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, mission_summary)

    return {
        "STATUS": "WORKTREE_OWNERSHIP_BOUND",
        "RECEIPTS": receipts,
        "WORKTREE_DELTA_MAP": path_to_wi,
    }


def load_ownership_receipt(
    repo_root: Path,
    mission_id: str,
    work_item_id: str,
) -> dict[str, Any] | None:
    path = _receipt_path(repo_root, mission_id, work_item_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def require_receipts_for_s4(
    repo_root: Path,
    mission_id: str,
    work_items: list[dict[str, Any]],
) -> tuple[bool, str, list[str]]:
    """Проверить наличие per-WI receipts перед S4 для repair-классов."""

    missing: list[str] = []
    for item in work_items:
        if item.get("route") not in {"MUST_FIX_REPAIR", "MIXED_SPLIT"} and not item.get("s4_repair_required"):
            continue
        wi_id = str(item.get("work_item_id") or item["finding_id"])
        if load_ownership_receipt(repo_root, mission_id, wi_id) is None:
            missing.append(wi_id)
    if missing:
        return False, "missing_worktree_ownership_receipt", missing
    return True, "ok", []
