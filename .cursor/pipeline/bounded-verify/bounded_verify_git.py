# -*- coding: utf-8 -*-
"""Git helpers for bounded-verify input manifest (worktree-safe)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_available(root: Path) -> bool:
    git_marker = root / ".git"
    return git_marker.is_dir() or git_marker.is_file()


def git_head(root: Path) -> str | None:
    if not git_available(root):
        return None
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def git_lines(root: Path, args: list[str]) -> tuple[str, ...]:
    if not git_available(root):
        return ()
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ()
    lines = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    return tuple(sorted(set(lines)))


def resolve_coverage_base_sha(root: Path, ref: str = "HEAD") -> str | None:
    """Explicit coverage audit base — no silent root-commit fallback."""
    import os

    explicit = os.environ.get("BOUNDED_VERIFY_COVERAGE_BASE_REF", "").strip()
    if explicit:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", explicit],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        return None
    for base_ref in ("origin/stage", "origin/main"):
        proc = subprocess.run(
            ["git", "-C", str(root), "merge-base", base_ref, ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def merge_base_with_head(root: Path, ref: str = "HEAD") -> str | None:
    """Best-effort merge base for manifests (may fall back to first root commit)."""
    resolved = resolve_coverage_base_sha(root, ref=ref)
    if resolved:
        return resolved
    roots = git_lines(root, ["rev-list", "--max-parents=0", "HEAD"])
    return roots[0] if roots else None
