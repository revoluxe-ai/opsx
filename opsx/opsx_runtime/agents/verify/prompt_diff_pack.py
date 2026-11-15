"""Embedded git diff для verify review hops (bugbot, security_review)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.verify.check_reuse import _comparison_scope_paths
from workflows.implementation.node_subprocess import run_subprocess_utf8


def collect_baseline_diff_pack(
    repo: Path,
    role: str,
    *,
    max_diff_chars: int | None = None,
) -> dict[str, Any]:
    """Имена затронутых путей и полный diff vs ``stage``. ``max_diff_chars`` не режет."""
    _ = max_diff_chars
    pack: dict[str, Any] = {
        "baseline": "stage",
        "touched_paths": [],
        "diff_snippet": "",
        "diff_truncated": False,
    }
    if not repo.is_dir():
        return pack
    included = _comparison_scope_paths(repo, role)
    for baseline in ("stage", "origin/stage"):
        probe = run_subprocess_utf8(
            ["git", "rev-parse", "--verify", "--quiet", baseline + "^{commit}"],
            cwd=repo,
            timeout=8,
        )
        if probe.returncode != 0:
            continue
        names = run_subprocess_utf8(
            ["git", "diff", "--name-only", baseline, "--", *included],
            cwd=repo,
            timeout=20,
        )
        if names.returncode == 0 and names.stdout.strip():
            pack["touched_paths"] = [
                ln.strip() for ln in names.stdout.splitlines() if ln.strip()
            ]
        diff = run_subprocess_utf8(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--no-color",
                baseline,
                "--",
                *included,
                ":(exclude)tests/opsx",
            ],
            cwd=repo,
            timeout=30,
        )
        if diff.returncode == 0:
            pack["diff_snippet"] = diff.stdout
        pack["baseline"] = baseline
        break
    return pack
