"""WORKTREE inventory для REQUIRED_CHANGE_SCOPE (fail-closed baseline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[3]
_BV_DIR = REPO_ROOT / ".cursor/pipeline/bounded-verify"
if str(_BV_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_BV_DIR))

from bounded_verify_capabilities import product_source_roots  # noqa: E402
from bounded_verify_git import git_available, git_lines, resolve_coverage_base_sha  # noqa: E402

InventoryStatus = Literal["RESOLVED", "BASE_UNRESOLVED", "GIT_UNAVAILABLE"]


def _in_policy_product_path(path: str, product_prefixes: tuple[str, ...]) -> bool:
    if not path.endswith(".py"):
        return False
    norm = path.replace("\\", "/").lstrip("./")
    return any(
        norm.startswith(prefix.rstrip("/") + "/") or norm == prefix.rstrip("/")
        for prefix in product_prefixes
    )


def resolve_required_change_inventory(root: Path) -> dict[str, Any]:
    """
    Собирает in-policy changed `.py` без фильтра через configured ignore.

    Returns:
        status RESOLVED с paths и base_sha, либо BASE_UNRESOLVED / GIT_UNAVAILABLE.
    """
    if not git_available(root):
        return {
            "status": "GIT_UNAVAILABLE",
            "reason": "GIT_UNAVAILABLE",
            "paths": (),
            "base_sha": None,
        }
    merge_base = resolve_coverage_base_sha(root)
    if not merge_base:
        return {
            "status": "BASE_UNRESOLVED",
            "reason": "GIT_BASE_UNRESOLVED",
            "paths": (),
            "base_sha": None,
        }
    product_prefixes = tuple(product_source_roots(root=root))
    candidates: set[str] = set()
    candidates.update(git_lines(root, ["diff", "--name-only", f"{merge_base}...HEAD"]))
    candidates.update(git_lines(root, ["diff", "--name-only"]))
    candidates.update(git_lines(root, ["diff", "--cached", "--name-only"]))
    candidates.update(git_lines(root, ["ls-files", "-o", "--exclude-standard"]))
    deleted = set(git_lines(root, ["diff", "--name-only", "--diff-filter=D"]))
    deleted.update(git_lines(root, ["diff", "--cached", "--name-only", "--diff-filter=D"]))
    existing = sorted(p for p in candidates if p not in deleted)
    paths = tuple(
        sorted(p for p in existing if _in_policy_product_path(p, product_prefixes)),
    )
    return {
        "status": "RESOLVED",
        "reason": None,
        "paths": paths,
        "base_sha": merge_base,
    }


def required_change_paths(root: Path) -> tuple[str, ...]:
    """Совместимость: пустой tuple при неразрешённом baseline (см. inventory)."""
    inv = resolve_required_change_inventory(root)
    if inv["status"] != "RESOLVED":
        return ()
    return tuple(inv["paths"])
