"""
Shared post-repair commit materialization for adversarial Temporal lanes.

BugBot, Security, and Project Bug Hunt share one transition:

  repair_result → durable progress → materialize_if_needed() → refresh HEAD
  → re-review the new committed candidate

Git writes belong exclusively to `/opsx-commit-all GO`. This module only
invokes that primitive via the Cursor adapter and reloads HEAD/preflight
identity. It must not `git add` / `git commit`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import commit_all_prompt_for_materialize
from finding_filter import is_product_repo_path
from models import RepairRunResult

COMMIT_ALL_COMPLETE = "COMPLETE"
COMMIT_ALL_COMPLETE_NOOP = "COMPLETE_NOOP"
COMMIT_MATERIALIZE_BLOCKED = "COMMIT_MATERIALIZE_BLOCKED"

_STATUS_TOKEN = r"(?:COMPLETE_NOOP|COMPLETE|PARTIAL|BLOCKED|FAIL_CLOSED|FAIL)"
_STATUS_RE = re.compile(
    rf"^STATUS:\s*({_STATUS_TOKEN})\b",
    re.MULTILINE | re.IGNORECASE,
)
_PARENT_STATUS_RE = re.compile(
    rf"PARENT_STATUS:\s*(?:\*\*)?\s*`?\s*({_STATUS_TOKEN})\b",
    re.IGNORECASE,
)


@dataclass
class MaterializeResult:
    ok: bool
    status: str
    head_before: str | None = None
    head_after: str | None = None
    tree_clean: bool = True
    candidate_transitioned: bool = False
    product_candidate_transitioned: bool = False
    invoked: bool = False
    raw_output: str = ""
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "head_before": self.head_before,
            "head_after": self.head_after,
            "tree_clean": self.tree_clean,
            "candidate_transitioned": self.candidate_transitioned,
            "product_candidate_transitioned": self.product_candidate_transitioned,
            "invoked": self.invoked,
            "raw_output": self.raw_output,
            "reason": self.reason,
        }


def _git_command() -> list[str]:
    """На Windows предпочитаем git.cmd для post-hop probe (не SDK agent shell)."""

    if os.name == "nt":
        for candidate in (
            shutil.which("git.cmd"),
            r"C:\Program Files\Git\cmd\git.exe",
        ):
            if candidate and Path(candidate).is_file():
                return [candidate]
    return ["git"]


def _run_git(repo_path: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_git_command(), *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def current_head(repo_path: str | Path) -> str | None:
    proc = _run_git(repo_path, "rev-parse", "HEAD")
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha or None


def is_tree_clean(repo_path: str | Path) -> bool:
    proc = _run_git(repo_path, "status", "--porcelain")
    if proc.returncode != 0:
        return False
    return not any(line.strip() for line in (proc.stdout or "").splitlines())


def dirty_paths(repo_path: str | Path) -> tuple[str, ...]:
    proc = _run_git(repo_path, "status", "--porcelain")
    if proc.returncode != 0:
        return ()
    paths: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            paths.append(path.replace("\\", "/"))
    return tuple(paths)


def has_durable_side_effects(repo_path: str | Path) -> bool:
    return not is_tree_clean(repo_path)


def has_product_or_test_dirty(repo_path: str | Path) -> bool:
    return any(is_product_repo_path(path) for path in dirty_paths(repo_path))


def is_product_tree_clean(repo_path: str | Path) -> bool:
    """Materialize gate: product/tests must be clean; runtime ledger untracked is OK."""

    return not has_product_or_test_dirty(repo_path)


def parse_commit_all_status(raw_output: str) -> str | None:
    """Разбор финального статуса hop ``/opsx-commit-all`` для materialize gate."""

    text = raw_output or ""
    status_matches = list(_STATUS_RE.finditer(text))
    if status_matches:
        return status_matches[-1].group(1).upper()
    parent = _PARENT_STATUS_RE.search(text)
    if parent:
        return parent.group(1).upper()
    return None


def needs_commit_all(repair: RepairRunResult, repo_path: str | Path | None) -> bool:
    """Invoke commit-all when repair made durable progress or the tree is dirty."""
    if not repo_path:
        return False
    if repair.repaired_this_batch is True:
        return True
    return has_durable_side_effects(repo_path)


def _noop(
    *,
    head: str | None,
    tree_clean: bool,
    reason: str,
    invoked: bool = False,
    raw_output: str = "",
) -> MaterializeResult:
    return MaterializeResult(
        ok=True,
        status=COMMIT_ALL_COMPLETE_NOOP,
        head_before=head,
        head_after=head,
        tree_clean=tree_clean,
        candidate_transitioned=False,
        product_candidate_transitioned=False,
        invoked=invoked,
        raw_output=raw_output,
        reason=reason,
    )


def _blocked(
    *,
    status: str,
    head_before: str | None,
    head_after: str | None,
    tree_clean: bool,
    invoked: bool,
    raw_output: str,
    reason: str,
) -> MaterializeResult:
    return MaterializeResult(
        ok=False,
        status=status,
        head_before=head_before,
        head_after=head_after,
        tree_clean=tree_clean,
        candidate_transitioned=False,
        product_candidate_transitioned=False,
        invoked=invoked,
        raw_output=raw_output,
        reason=reason,
    )


def materialize_if_needed(
    *,
    repo_path: str | Path | None,
    adapter: Any,
    repair: RepairRunResult,
    mock_mode: bool = False,
) -> MaterializeResult:
    """Run `/opsx-commit-all GO` when needed; refresh HEAD identity.

    mock_mode / missing repo: COMPLETE_NOOP without invoking the adapter
    (mock Temporal scenarios have no commit-all steps and must not mutate git).
    """
    if mock_mode or not repo_path:
        return _noop(head=None, tree_clean=True, reason="NO_REPO_OR_MOCK")

    head_before = current_head(repo_path)
    dirty = has_durable_side_effects(repo_path)
    product_dirty = has_product_or_test_dirty(repo_path)

    if not needs_commit_all(repair, repo_path):
        return _noop(
            head=head_before,
            tree_clean=not dirty,
            reason="NO_MATERIALIZE_REQUIRED",
        )

    if not dirty and repair.repaired_this_batch is True:
        # closed_fixed (or equivalent) with a clean tree: still invoke;
        # COMPLETE_NOOP is the allowed no-transition outcome.
        pass

    try:
        invocation = adapter.execute_prompt(
            commit_all_prompt_for_materialize(),
            step="commit-all",
        )
    except Exception as exc:  # adapter contract: fail closed, never review dirty
        return _blocked(
            status=COMMIT_MATERIALIZE_BLOCKED,
            head_before=head_before,
            head_after=current_head(repo_path),
            tree_clean=is_product_tree_clean(repo_path),
            invoked=True,
            raw_output=str(exc),
            reason="COMMIT_ALL_INVOCATION_FAILED",
        )

    raw = getattr(invocation, "raw_output", "") or ""
    parsed_status = parse_commit_all_status(raw)
    head_after = current_head(repo_path)
    product_clean = is_product_tree_clean(repo_path)

    if parsed_status is None:
        return _blocked(
            status=COMMIT_MATERIALIZE_BLOCKED,
            head_before=head_before,
            head_after=head_after,
            tree_clean=product_clean,
            invoked=True,
            raw_output=raw,
            reason="COMMIT_ALL_STATUS_UNPARSEABLE",
        )

    if parsed_status not in {COMMIT_ALL_COMPLETE, COMMIT_ALL_COMPLETE_NOOP}:
        return _blocked(
            status=COMMIT_MATERIALIZE_BLOCKED,
            head_before=head_before,
            head_after=head_after,
            tree_clean=product_clean,
            invoked=True,
            raw_output=raw,
            reason=f"COMMIT_ALL_{parsed_status}",
        )

    if not product_clean:
        return _blocked(
            status=COMMIT_MATERIALIZE_BLOCKED,
            head_before=head_before,
            head_after=head_after,
            tree_clean=False,
            invoked=True,
            raw_output=raw,
            reason="TREE_DIRTY_AFTER_COMMIT_ALL",
        )

    if parsed_status == COMMIT_ALL_COMPLETE_NOOP:
        return MaterializeResult(
            ok=True,
            status=COMMIT_ALL_COMPLETE_NOOP,
            head_before=head_before,
            head_after=head_after or head_before,
            tree_clean=True,
            candidate_transitioned=False,
            product_candidate_transitioned=False,
            invoked=True,
            raw_output=raw,
            reason="COMPLETE_NOOP",
        )

    transitioned = bool(head_before and head_after and head_before != head_after)
    return MaterializeResult(
        ok=True,
        status=COMMIT_ALL_COMPLETE,
        head_before=head_before,
        head_after=head_after,
        tree_clean=True,
        candidate_transitioned=transitioned,
        product_candidate_transitioned=transitioned and product_dirty,
        invoked=True,
        raw_output=raw,
        reason="COMPLETE",
    )
