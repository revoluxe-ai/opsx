"""Effective static coverage audit и минимальная активация pyright strict."""

from __future__ import annotations

from opsx.project_context import resource_path

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

StaticDisposition = Literal[
    "ALREADY_COVERED",
    "MISSING_FROM_INCLUDE",
    "HIT_EXCLUDE",
    "EXCLUDE_OVERRIDES_INCLUDE",
    "PYRIGHT_IGNORED",
    "NOT_PRODUCTION",
    "DELETED",
]


@dataclass(frozen=True)
class StaticPathAudit:
    path: str
    disposition: StaticDisposition
    detail: str


def _matches_glob(path: str, pattern: str) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/").lstrip("./")
    if pat.endswith("/"):
        pat = pat.rstrip("/")
    if norm == pat or norm.startswith(pat + "/"):
        return True
    return fnmatch.fnmatch(norm, pat)


def _in_include(path: str, include: list[str]) -> bool:
    return any(_matches_glob(path, inc) for inc in include)


def _hit_exclude(path: str, exclude: list[str]) -> bool:
    return any(_matches_glob(path, ex) for ex in exclude)


def load_strict_config(repo_root: Path) -> tuple[Path | None, dict[str, Any]]:
    for name in ("pyrightconfig_strict.json", "pyrightconfig.json"):
        path = resource_path(repo_root, name)
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return path, raw
    return None, {}


def effective_static_disposition(
    path: str,
    *,
    include: list[str],
    exclude: list[str],
    ignore: list[str],
) -> StaticPathAudit:
    """Exclude перекрывает include (exclude-over-include)."""
    norm = path.replace("\\", "/").lstrip("./")
    if norm in ignore or any(_matches_glob(norm, ig) for ig in ignore):
        return StaticPathAudit(path=norm, disposition="PYRIGHT_IGNORED", detail="pyright ignore[]")
    in_inc = _in_include(norm, include)
    in_ex = _hit_exclude(norm, exclude)
    if in_inc and in_ex:
        return StaticPathAudit(
            path=norm,
            disposition="EXCLUDE_OVERRIDES_INCLUDE",
            detail="path matches include and exclude; exclude wins",
        )
    if in_ex:
        return StaticPathAudit(path=norm, disposition="HIT_EXCLUDE", detail="exclude[]")
    if in_inc:
        return StaticPathAudit(path=norm, disposition="ALREADY_COVERED", detail="include[]")
    return StaticPathAudit(path=norm, disposition="MISSING_FROM_INCLUDE", detail="not in include[]")


def audit_static_paths(
    repo_root: Path,
    paths: tuple[str, ...],
    *,
    deleted_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """READ_ONLY audit для production paths."""
    config_path, raw = load_strict_config(repo_root)
    if config_path is None:
        return {
            "STATUS": "BLOCKED",
            "REASON": "PYRIGHT_STRICT_CONFIG_MISSING",
            "checker": "pyright",
        }
    include = [str(x) for x in raw.get("include", [])]
    exclude = [str(x) for x in raw.get("exclude", [])]
    ignore = [str(x) for x in raw.get("ignore", [])]
    rows: list[dict[str, str]] = []
    already = 0
    need_activation = 0
    blocked_wide = 0
    for path in paths:
        if path in deleted_paths:
            rows.append(
                {
                    "path": path,
                    "disposition": "DELETED",
                    "detail": "deleted in worktree",
                },
            )
            continue
        audit = effective_static_disposition(
            path,
            include=include,
            exclude=exclude,
            ignore=ignore,
        )
        rows.append(
            {
                "path": audit.path,
                "disposition": audit.disposition,
                "detail": audit.detail,
            },
        )
        if audit.disposition == "ALREADY_COVERED":
            already += 1
        elif audit.disposition in ("MISSING_FROM_INCLUDE", "PYRIGHT_IGNORED"):
            need_activation += 1
        elif audit.disposition in ("HIT_EXCLUDE", "EXCLUDE_OVERRIDES_INCLUDE"):
            blocked_wide += 1
    return {
        "STATUS": "READY",
        "config_path": str(config_path.relative_to(repo_root)),
        "checker": "pyright",
        "rows": rows,
        "already_covered_count": already,
        "need_activation_count": need_activation,
        "blocked_exclude_count": blocked_wide,
    }


def build_static_activation_plan(
    audit: Mapping[str, Any],
    *,
    allow_narrow_exclude: bool,
) -> dict[str, Any]:
    """Минимальный план: additive include и точечное снятие ignore."""
    rows = audit.get("rows")
    if not isinstance(rows, list):
        return {"include_add": [], "ignore_remove": [], "exclude_narrow": [], "blocked": []}
    include_add: list[str] = []
    ignore_remove: list[str] = []
    exclude_narrow: list[str] = []
    blocked: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        disp = str(row.get("disposition") or "")
        if disp == "MISSING_FROM_INCLUDE":
            include_add.append(path)
        elif disp == "PYRIGHT_IGNORED":
            ignore_remove.append(path)
        elif disp in ("HIT_EXCLUDE", "EXCLUDE_OVERRIDES_INCLUDE"):
            if allow_narrow_exclude:
                exclude_narrow.append(path)
            else:
                blocked.append(path)
    return {
        "include_add": sorted(set(include_add)),
        "ignore_remove": sorted(set(ignore_remove)),
        "exclude_narrow": sorted(set(exclude_narrow)),
        "blocked": sorted(set(blocked)),
    }


def apply_static_activation_plan(repo_root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Применить минимальные правки ``pyrightconfig_strict.json``."""
    config_path, raw = load_strict_config(repo_root)
    if config_path is None:
        return {"applied": False, "reason": "PYRIGHT_STRICT_CONFIG_MISSING"}
    include = [str(x) for x in raw.get("include", [])]
    ignore = [str(x) for x in raw.get("ignore", [])]
    changed = False
    for path in plan.get("include_add") or []:
        if path not in include:
            include.append(path)
            changed = True
    for path in plan.get("ignore_remove") or []:
        if path in ignore:
            ignore.remove(path)
            changed = True
        else:
            ignore = [ig for ig in ignore if ig != path]
    if changed:
        raw["include"] = include
        raw["ignore"] = ignore
        config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return {
        "applied": changed,
        "config_path": str(config_path.relative_to(repo_root)),
        "include_add": plan.get("include_add") or [],
        "ignore_remove": plan.get("ignore_remove") or [],
    }
