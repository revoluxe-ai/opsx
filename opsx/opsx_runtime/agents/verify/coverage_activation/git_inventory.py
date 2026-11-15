"""Детерминированный Changed File Inventory для coverage activation."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from agents.check_receipts import content_digest
from workflows.implementation.node_subprocess import run_subprocess_utf8


@dataclass(frozen=True)
class ChangeInventory:
    """Снимок файлов change: текущий diff и historical touched отдельно."""

    base_sha: str
    audit_target: str
    current_changed: tuple[str, ...]
    historical_touched: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    status: str
    block_reason: str
    product_content_digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "audit_target": self.audit_target,
            "current_changed": list(self.current_changed),
            "historical_touched": list(self.historical_touched),
            "deleted_paths": list(self.deleted_paths),
            "status": self.status,
            "block_reason": self.block_reason,
            "product_content_digest": self.product_content_digest,
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "product_content_digest": self.product_content_digest,
                "current_changed": list(self.current_changed),
                "historical_touched": list(self.historical_touched),
                "deleted_paths": list(self.deleted_paths),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _git_available(root: Path) -> bool:
    marker = resource_path(root, ".git")
    return marker.is_dir() or marker.is_file()


def _git_lines(root: Path, args: list[str]) -> tuple[str, ...]:
    if not _git_available(root):
        return ()
    proc = run_subprocess_utf8(["git", "-C", str(root), *args])
    if proc.returncode != 0:
        return ()
    lines = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    return tuple(sorted(set(lines)))


def resolve_coverage_base_sha(root: Path, *, ref: str | None = None) -> str | None:
    """Resolve comparison baseline directly, without reading Git HEAD.

    A moving working branch must not change the set of product files that
    coverage activation considers. The baseline's *content* remains relevant.
    """
    import os

    _ = ref  # Compatibility with existing callers; never used as a HEAD ref.
    explicit = os.environ.get("BOUNDED_VERIFY_COVERAGE_BASE_REF", "").strip()
    if explicit in ("HEAD", "@", ""):
        explicit = "" if not explicit else "INVALID"
    candidates = (explicit,) if explicit else ("origin/stage", "origin/main")
    for base_ref in candidates:
        if base_ref == "INVALID":
            return None
        probe = run_subprocess_utf8(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", base_ref + "^{commit}"],
        )
        if probe.returncode == 0 and len(probe.stdout.strip()) == 40:
            return probe.stdout.strip()
    return None


def product_source_prefixes(root: Path) -> tuple[str, ...]:
    """Корни production-кода из ``opsx/project/capabilities.yaml``."""
    cap_path = resource_path(root, "opsx/project/capabilities.yaml")
    if not cap_path.is_file():
        return ("src/stroy",)
    raw = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return ("src/stroy",)
    product = raw.get("product")
    if not isinstance(product, dict):
        return ("src/stroy",)
    roots = product.get("source_roots")
    if not isinstance(roots, list):
        return ("src/stroy",)
    out = tuple(str(r).replace("\\", "/").strip("/") for r in roots if str(r).strip())
    return out or ("src/stroy",)


def _in_policy_path(path: str, prefixes: Sequence[str]) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    if norm == "opsx" or norm.startswith("opsx/") or norm.startswith("tests/opsx/"):
        return False
    if not norm.endswith(".py"):
        return False
    return any(norm.startswith(p.rstrip("/") + "/") or norm == p.rstrip("/") for p in prefixes)


def _rename_pairs(root: Path, merge_base: str) -> dict[str, str]:
    """Старый путь → новый для rename (``R`` / ``C``)."""
    proc = run_subprocess_utf8(
        ["git", "-C", str(root), "diff", "--name-status", "-M", merge_base, "--"],
    )
    mapping: dict[str, str] = {}
    if proc.returncode != 0:
        return mapping
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            mapping[parts[1].strip().replace("\\", "/")] = parts[2].strip().replace("\\", "/")
    return mapping


def collect_change_inventory(
    repo_root: Path,
    *,
    prior_accumulated: Sequence[str] | None = None,
) -> ChangeInventory:
    """Inventory base→worktree; historical = ранее виденные, но не в текущем diff."""
    if not _git_available(repo_root):
        return ChangeInventory(
            base_sha="",
            audit_target="WORKTREE",
            current_changed=(),
            historical_touched=(),
            deleted_paths=(),
            status="BLOCKED",
            block_reason="GIT_UNAVAILABLE",
        )
    merge_base = resolve_coverage_base_sha(repo_root)
    if not merge_base:
        return ChangeInventory(
            base_sha="",
            audit_target="WORKTREE",
            current_changed=(),
            historical_touched=(),
            deleted_paths=(),
            status="BLOCKED",
            block_reason="GIT_BASE_UNRESOLVED",
        )
    prefixes = product_source_prefixes(repo_root)
    candidates: set[str] = set()
    # Compare one explicit baseline tree against current working-tree bytes.
    # No three-dot merge-base and no implicit HEAD for staged/unstaged files.
    candidates.update(_git_lines(repo_root, ["diff", "--name-only", merge_base, "--"]))
    candidates.update(_git_lines(repo_root, ["ls-files", "-o", "--exclude-standard"]))
    deleted = set(_git_lines(repo_root, ["diff", "--name-only", "--diff-filter=D", merge_base, "--"]))
    renames = _rename_pairs(repo_root, merge_base)
    normalized: set[str] = set()
    for path in candidates:
        if path in deleted:
            continue
        norm = renames.get(path, path)
        if _in_policy_path(norm, prefixes):
            normalized.add(norm)
    current = tuple(sorted(normalized))
    accumulated = set(prior_accumulated or ())
    accumulated.update(current)
    historical = tuple(sorted(p for p in accumulated if p not in set(current)))
    deleted_policy = tuple(sorted(p for p in deleted if _in_policy_path(p, prefixes)))
    # Unlike a path-only or Git-commit marker, this detects edits even when
    # the same file set remains changed relative to the comparison baseline.
    product_files: set[Path] = set()
    for prefix in (*prefixes, "tests", "migrations"):
        scope = resource_path(repo_root, prefix)
        if scope.is_dir():
            product_files.update(
                p for p in scope.rglob("*.py")
                if p.is_file() and p.relative_to(repo_root).parts[:2] != ("tests", "opsx")
            )
    for config in ("pyproject.toml", "poetry.lock", "mypy.ini", "pytest.ini",
                   "pyrightconfig_strict.json", "setup.cfg"):
        p = resource_path(repo_root, config)
        if p.is_file():
            product_files.add(p)
    try:
        product_digest = content_digest(repo_root, product_files)
    except (ValueError, OSError):
        product_digest = ""
    if not product_digest:
        status, block_reason = "BLOCKED", "COVERAGE_PRODUCT_INPUT_UNBOUND"
    elif not current and not historical:
        status, block_reason = "NOT_APPLICABLE", ""
    else:
        status, block_reason = "READY", ""
    return ChangeInventory(
        base_sha=merge_base,
        audit_target="WORKTREE",
        current_changed=current,
        historical_touched=historical,
        deleted_paths=deleted_policy,
        status=status,
        block_reason=block_reason,
        product_content_digest=product_digest,
    )


def merge_prior_accumulated(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Восстановить накопленные пути из GraphState и commit/repair артефактов."""
    seen: set[str] = set()
    inv = state.get("coverage_activation_inventory")
    if isinstance(inv, dict):
        for key in ("current_changed", "historical_touched", "accumulated_touched"):
            raw = inv.get(key)
            if isinstance(raw, list):
                seen.update(str(p) for p in raw)
    commit_files = state.get("commit_files")
    if isinstance(commit_files, list):
        seen.update(str(p) for p in commit_files)
    fix = state.get("verify_finding_fix")
    if isinstance(fix, dict):
        files = fix.get("files_changed")
        if isinstance(files, list):
            seen.update(str(p) for p in files)
    prior = state.get("coverage_activation_accumulated_touched")
    if isinstance(prior, list):
        seen.update(str(p) for p in prior)
    return tuple(sorted(seen))
