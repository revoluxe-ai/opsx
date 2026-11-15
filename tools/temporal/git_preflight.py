"""Collect branch diff evidence via local git before Cursor review.

Bugbot uses full product-lane preflight. Security uses scoped preflight
(`src/**`, `tests/**` only) so high-tier review is not blocked by process diff.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import (
    GIT_PREFLIGHT_ARTIFACT_REL,
    GIT_PREFLIGHT_MAX_FILE_DIFF_CHARS,
    GIT_PREFLIGHT_MAX_FILES_WITH_DIFF,
    GIT_PREFLIGHT_MAX_STAT_CHARS,
    MAX_REVIEW_PROMPT_CHARS,
    REVIEW_BASE_REF,
    SECURITY_REVIEW_PATH_PREFIXES,
)
from finding_filter import is_product_repo_path, matches_path_prefixes, product_lane_rank


@dataclass(frozen=True)
class GitPreflightResult:
    ok: bool
    base_ref: str
    head_ref: str | None = None
    merge_base: str | None = None
    diff_stat: str = ""
    diff_stat_lines: int = 0
    changed_files: tuple[str, ...] = ()
    file_diffs: tuple[tuple[str, str], ...] = ()
    artifact_path: str | None = None
    error: str | None = None
    review_scope: str = "FULL_BRANCH_DIFF"
    path_prefixes: tuple[str, ...] = ()


def _run_git(repo_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _truncate_text(text: str, limit: int, *, note: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{note}]"


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_review_noise_path(path: str) -> bool:
    norm = _normalize_repo_path(path)
    return (
        "/__pycache__/" in norm
        or norm.startswith("__pycache__/")
        or norm.endswith(".pyc")
    )


def prioritize_changed_files_for_diffs(
    changed_files: tuple[str, ...] | list[str],
    *,
    max_files: int = GIT_PREFLIGHT_MAX_FILES_WITH_DIFF,
    path_prefixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Cap file-diff snippets to review scope (product lane or src/tests)."""
    if path_prefixes:
        scoped = [
            path
            for path in changed_files
            if not _is_review_noise_path(path) and matches_path_prefixes(path, path_prefixes)
        ]
        return sorted(scoped, key=product_lane_rank)[:max_files]

    product = [
        path
        for path in changed_files
        if not _is_review_noise_path(path) and is_product_repo_path(path)
    ]
    return sorted(product, key=product_lane_rank)[:max_files]


def _pathspec_args(path_prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Map repo prefixes (`src/`, `tests/`) to git pathspec directory args."""
    args: list[str] = []
    for prefix in path_prefixes:
        segment = prefix.rstrip("/")
        if segment and segment not in args:
            args.append(segment)
    return tuple(args)


def _review_scope_label(path_prefixes: tuple[str, ...] | None) -> str:
    if path_prefixes == SECURITY_REVIEW_PATH_PREFIXES:
        return "SRC_TESTS_ONLY"
    return "FULL_BRANCH_DIFF"


def _collect_untracked_files(repo: str) -> tuple[str, ...]:
    proc = _run_git(repo, "ls-files", "--others", "--exclude-standard")
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip() and not _is_review_noise_path(line.strip()))


def _merge_changed_file_lists(*lists: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    merged: list[str] = []
    for items in lists:
        for path in items:
            if path and path not in seen and not _is_review_noise_path(path):
                seen.add(path)
                merged.append(path)
    return tuple(merged)


def collect_git_preflight(
    repo_path: str | Path,
    *,
    base_ref: str = REVIEW_BASE_REF,
    path_prefixes: tuple[str, ...] | None = None,
) -> GitPreflightResult:
    """Run merge-base..working-tree git evidence collection outside the Cursor agent."""
    repo = str(Path(repo_path).resolve())
    scope_prefixes = path_prefixes or ()
    review_scope = _review_scope_label(path_prefixes)
    pathspec = _pathspec_args(scope_prefixes) if scope_prefixes else ()

    verify = _run_git(repo, "rev-parse", "--verify", base_ref)
    if verify.returncode != 0:
        return GitPreflightResult(
            ok=False,
            base_ref=base_ref,
            error=f"git rev-parse --verify {base_ref} failed: {(verify.stderr or verify.stdout).strip()}",
        )

    head = _run_git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        return GitPreflightResult(
            ok=False,
            base_ref=base_ref,
            error=f"git rev-parse HEAD failed: {(head.stderr or head.stdout).strip()}",
        )

    merge_base = _run_git(repo, "merge-base", base_ref, "HEAD")
    if merge_base.returncode != 0:
        return GitPreflightResult(
            ok=False,
            base_ref=base_ref,
            head_ref=head.stdout.strip(),
            error=f"git merge-base failed: {(merge_base.stderr or merge_base.stdout).strip()}",
        )

    merge_base_sha = merge_base.stdout.strip()
    # merge-base..working-tree (not stage...HEAD): branch commits + dirty tracked files.
    stat_args = ("diff", "--stat", merge_base_sha, *pathspec)
    stat = _run_git(repo, *stat_args)
    if stat.returncode != 0:
        return GitPreflightResult(
            ok=False,
            base_ref=base_ref,
            head_ref=head.stdout.strip(),
            merge_base=merge_base_sha,
            error=f"git diff --stat failed: {(stat.stderr or stat.stdout).strip()}",
        )

    stat_text = stat.stdout.strip()
    stat_lines = len([line for line in stat_text.splitlines() if line.strip()])

    name_only = _run_git(repo, "diff", "--name-only", merge_base_sha, *pathspec)
    tracked_changed = tuple(
        line.strip()
        for line in name_only.stdout.splitlines()
        if line.strip()
    )
    untracked = _collect_untracked_files(repo)
    if scope_prefixes:
        untracked = tuple(
            path for path in untracked if matches_path_prefixes(path, scope_prefixes)
        )
    changed_files = _merge_changed_file_lists(tracked_changed, untracked)

    file_diffs: list[tuple[str, str]] = []
    for path in prioritize_changed_files_for_diffs(
        changed_files,
        path_prefixes=path_prefixes,
    ):
        diff = _run_git(repo, "diff", merge_base_sha, "--", path)
        if diff.returncode != 0:
            continue
        body = diff.stdout.strip()
        if len(body) > GIT_PREFLIGHT_MAX_FILE_DIFF_CHARS:
            body = _truncate_text(
                body,
                GIT_PREFLIGHT_MAX_FILE_DIFF_CHARS,
                note="truncated by POC git preflight",
            )
        file_diffs.append((path, body))

    return GitPreflightResult(
        ok=True,
        base_ref=base_ref,
        head_ref=head.stdout.strip(),
        merge_base=merge_base_sha,
        diff_stat=stat_text,
        diff_stat_lines=stat_lines,
        changed_files=changed_files,
        file_diffs=tuple(file_diffs),
        review_scope=review_scope,
        path_prefixes=scope_prefixes,
    )


def format_git_preflight_artifact(preflight: GitPreflightResult) -> str:
    """Full git evidence for on-disk artifact (agent reads via Read tool)."""
    stat_block = _truncate_text(
        preflight.diff_stat or "(empty)",
        GIT_PREFLIGHT_MAX_STAT_CHARS,
        note="diff --stat truncated in artifact",
    )
    lines = [
        "POC_GIT_PREFLIGHT: VERIFIED",
        f"REVIEW_SCOPE: {preflight.review_scope}",
        f"BASE_REF: {preflight.base_ref}",
        f"HEAD_REF: {preflight.head_ref}",
        f"MERGE_BASE: {preflight.merge_base}",
        "WORKTREE_SCOPE: merge-base..working-tree",
        f"DIFF_STAT_LINES: {preflight.diff_stat_lines}",
        f"CHANGED_FILES: {len(preflight.changed_files)}",
        "",
        "## git diff --stat",
        "```",
        stat_block,
        "```",
        "",
        "## changed files",
    ]
    lines.extend(preflight.changed_files or ["(none)"])

    for path, diff in preflight.file_diffs:
        lines.extend(
            [
                "",
                f"## file diff: {path}",
                "```diff",
                diff or "(empty)",
                "```",
            ]
        )

    remaining = len(preflight.changed_files) - len(preflight.file_diffs)
    if remaining > 0:
        lines.append(
            f"\n({remaining} more changed file(s) omitted from artifact; use git diff or Read as needed.)"
        )
    return "\n".join(lines)


def write_git_preflight_artifact(repo_path: str | Path, preflight: GitPreflightResult) -> str:
    """Persist full preflight evidence under the repo and return repo-relative path."""
    repo = Path(repo_path).resolve()
    artifact_rel = GIT_PREFLIGHT_ARTIFACT_REL
    artifact_path = resource_path(repo, artifact_rel)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(format_git_preflight_artifact(preflight), encoding="utf-8")
    return artifact_rel.replace("\\", "/")


def format_git_preflight_prompt_summary(preflight: GitPreflightResult, artifact_rel_path: str) -> str:
    """Compact preflight block safe for Windows agent CLI argv limits."""
    preview_files = ", ".join(preflight.changed_files[:12])
    if len(preflight.changed_files) > 12:
        preview_files += f", ... (+{len(preflight.changed_files) - 12} more)"
    return "\n".join(
        [
            "POC_GIT_PREFLIGHT: VERIFIED",
            f"REVIEW_SCOPE: {preflight.review_scope}",
            f"PREFLIGHT_ARTIFACT: {artifact_rel_path}",
            f"BASE_REF: {preflight.base_ref}",
            f"HEAD_REF: {preflight.head_ref}",
            f"MERGE_BASE: {preflight.merge_base}",
            f"DIFF_STAT_LINES: {preflight.diff_stat_lines}",
            f"CHANGED_FILES: {len(preflight.changed_files)}",
            f"CHANGED_FILES_PREVIEW: {preview_files or '(none)'}",
            "",
            f"Сначала Read `{artifact_rel_path}` — там полный diff --stat и file diffs.",
            "Shell для git не нужен. Не пиши про hooks/shell blocked.",
            "If REVIEW_SCOPE is SRC_TESTS_ONLY: Base Branch is BASE_REF above"
            " (never origin/HEAD/master). Prefer this artifact over subagent"
            " branch-diff; if subagent cannot compute branch-changes, FALLBACK"
            " to summarize from this artifact and still emit the required"
            " Security review summary phrases/table.",
        ]
    )


def build_review_prompt(
    preflight: GitPreflightResult | None = None,
    *,
    repo_path: str | Path | None = None,
    review_prompt: str | None = None,
) -> str:
    """Build the review prompt with compact summary + on-disk artifact reference."""
    from config import REVIEW_PROMPT

    base_prompt = review_prompt if review_prompt is not None else REVIEW_PROMPT

    if preflight is None or not preflight.ok:
        return base_prompt

    if repo_path is None:
        raise ValueError("repo_path is required when git preflight succeeded")

    artifact_rel = write_git_preflight_artifact(repo_path, preflight)
    summary = format_git_preflight_prompt_summary(preflight, artifact_rel)
    prompt = f"""{base_prompt}

---
{summary}
"""
    if len(prompt) > MAX_REVIEW_PROMPT_CHARS:
        prompt = f"""{base_prompt}

---
POC_GIT_PREFLIGHT: VERIFIED
REVIEW_SCOPE: {preflight.review_scope}
PREFLIGHT_ARTIFACT: {artifact_rel}
BASE_REF: {preflight.base_ref}
HEAD_REF: {preflight.head_ref}
DIFF_STAT_LINES: {preflight.diff_stat_lines}
CHANGED_FILES: {len(preflight.changed_files)}

Сначала Read `{artifact_rel}`. Shell для git не нужен.
If REVIEW_SCOPE is SRC_TESTS_ONLY and the security-review subagent cannot
compute branch-changes: FALLBACK — summarize from this artifact and emit the
required Security review summary phrases/table (blocker-only text forbidden).
"""
    return prompt
