# -*- coding: utf-8 -*-
"""Machine static-check coverage audit (`/opsx-static-check-coverage` semantics)."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bounded_verify_capabilities import product_source_roots
from bounded_verify_git import git_available, git_lines, resolve_coverage_base_sha

REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve_pyright_strict_config(root: Path) -> Path | None:
    """Locate strict pyright config at repo root."""
    for name in ("pyrightconfig_strict.json", "pyrightconfig.json"):
        path = root / name
        if path.is_file():
            return path
    return None


def _matches_glob(path: str, pattern: str) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/").lstrip("./")
    if pat.endswith("/"):
        pat = pat.rstrip("/")
    if norm == pat or norm.startswith(pat + "/"):
        return True
    return fnmatch.fnmatch(norm, pat)


def _path_covered(path: str, include: Sequence[str], exclude: Sequence[str]) -> str:
    """Returns COVERED | MISSING_FROM_INCLUDE | HIT_EXCLUDE."""
    norm = path.replace("\\", "/").lstrip("./")
    for ex in exclude:
        if _matches_glob(norm, ex):
            return "HIT_EXCLUDE"
    for inc in include:
        if _matches_glob(norm, inc):
            return "COVERED"
    return "MISSING_FROM_INCLUDE"


def _in_policy_product_path(path: str, product_prefixes: Sequence[str]) -> bool:
    if not path.endswith(".py"):
        return False
    norm = path.replace("\\", "/").lstrip("./")
    return any(norm.startswith(prefix.rstrip("/") + "/") or norm == prefix.rstrip("/") for prefix in product_prefixes)


def changed_in_policy_python_paths(root: Path) -> tuple[str, ...]:
    """WORKTREE scope: committed diff + index + tracked worktree + untracked."""
    product_prefixes = product_source_roots(root=root)
    merge_base = resolve_coverage_base_sha(root)
    if not merge_base:
        return ()
    candidates: set[str] = set()
    candidates.update(git_lines(root, ["diff", "--name-only", f"{merge_base}...HEAD"]))
    candidates.update(git_lines(root, ["diff", "--name-only"]))
    candidates.update(git_lines(root, ["diff", "--cached", "--name-only"]))
    candidates.update(git_lines(root, ["ls-files", "-o", "--exclude-standard"]))
    deleted = set(git_lines(root, ["diff", "--name-only", "--diff-filter=D"]))
    deleted.update(git_lines(root, ["diff", "--cached", "--name-only", "--diff-filter=D"]))
    existing = sorted(p for p in candidates if p not in deleted)
    return tuple(sorted(p for p in existing if _in_policy_product_path(p, product_prefixes)))


def pyright_audit_ready(root: Path) -> bool:
    """READY only when config, git, and merge-base resolve."""
    if resolve_pyright_strict_config(root) is None:
        return False
    if not git_available(root):
        return False
    return resolve_coverage_base_sha(root) is not None


def audit_pyright_strict_coverage(
    *,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """READ_ONLY coverage audit; not a typecheck run."""
    config_path = resolve_pyright_strict_config(root)
    if config_path is None:
        return {
            "STATUS": "BLOCKED",
            "REASON": "PYRIGHT_STRICT_CONFIG_MISSING",
            "evidence_contract": "STATIC_CHECK_COVERAGE_REPORT",
        }
    if not git_available(root):
        return {
            "STATUS": "BLOCKED",
            "REASON": "GIT_UNAVAILABLE",
            "evidence_contract": "STATIC_CHECK_COVERAGE_REPORT",
        }
    merge_base = resolve_coverage_base_sha(root)
    if not merge_base:
        return {
            "STATUS": "BLOCKED",
            "REASON": "GIT_BASE_UNRESOLVED",
            "evidence_contract": "STATIC_CHECK_COVERAGE_REPORT",
        }
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    include = [str(x) for x in raw.get("include", [])]
    exclude = [str(x) for x in raw.get("exclude", [])]
    paths = changed_in_policy_python_paths(root)
    if not paths:
        return {
            "STATUS": "PASS",
            "SCOPE": "NOT_APPLICABLE",
            "evidence_contract": "STATIC_CHECK_COVERAGE_REPORT",
            "BASE_SHA": merge_base,
            "audited_paths": [],
            "gaps": [],
            "AUDIT_TARGET": "WORKTREE",
        }
    rows: list[dict[str, str]] = []
    for path in paths:
        disposition = _path_covered(path, include, exclude)
        if disposition != "COVERED":
            rows.append({"path": path, "disposition": disposition})
    status = "PASS" if not rows else "FAIL"
    return {
        "STATUS": status,
        "evidence_contract": "STATIC_CHECK_COVERAGE_REPORT",
        "config_path": str(config_path.relative_to(root)),
        "BASE_SHA": merge_base,
        "audited_paths": list(paths),
        "gaps": rows,
        "AUDIT_TARGET": "WORKTREE",
    }


def run_pyright_strict_coverage_adapter(
    change_id: str,
    root: Path,
) -> tuple[str, Mapping[str, Any]]:
    """Adapter runner: PASS/FAIL/BLOCKED from coverage audit only."""
    report = audit_pyright_strict_coverage(root=root)
    status = str(report.get("STATUS", "BLOCKED"))
    packet = {**report, "obligation_id": "static-check-coverage", "change_id": change_id}
    if status == "BLOCKED":
        return "EXECUTION_ERROR", {**packet, "code": report.get("REASON", "BLOCKED")}
    if status == "PASS":
        return "PASS", packet
    return "FAIL", packet
