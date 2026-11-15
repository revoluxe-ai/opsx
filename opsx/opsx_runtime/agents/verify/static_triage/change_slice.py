"""Ограниченный git diff вокруг одного файла и cited line."""

from __future__ import annotations

import subprocess
from pathlib import Path

from workflows.implementation.node_subprocess import run_subprocess_utf8

_TRUNC_MARKER = "\n… [change_slice truncated — see git diff locally] …\n"
_MAX_SLICE_CHARS = 12_000
_CONTEXT_LINES = 40


def _run_git_diff(repo: Path, rel_path: str) -> tuple[str | None, bool]:
    """Вернуть unified diff одного файла или None если git недоступен."""
    try:
        proc = run_subprocess_utf8(
            ["git", "diff", "--", rel_path],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if proc.returncode not in (0, 1):
        return None, False
    text = (proc.stdout or "").strip()
    if not text:
        show = run_subprocess_utf8(
            ["git", "show", "HEAD", "--", rel_path],
            cwd=repo,
            timeout=30,
        )
        if show.returncode == 0 and (show.stdout or "").strip():
            return show.stdout, True
        return "", False
    return text, True


def _narrow_diff_hunks(diff_text: str, *, target_line: int | None) -> str:
    """Оставить hunks рядом с target_line (±context), иначе вернуть diff целиком."""
    if target_line is None or not diff_text:
        return diff_text
    lines = diff_text.splitlines()
    kept: list[str] = []
    hunk_start = 0
    include = False
    current_new_line = 0
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            if include and kept:
                kept.append("")
            hunk_start = i
            include = False
            parts = line.split("+")
            if len(parts) >= 2:
                try:
                    current_new_line = int(parts[1].split(",")[0])
                except ValueError:
                    current_new_line = 0
            kept.append(line)
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff "):
            kept.append(line)
            continue
        if line.startswith("+"):
            if abs(current_new_line - target_line) <= _CONTEXT_LINES:
                include = True
            current_new_line += 1
        elif line.startswith("-"):
            if abs(current_new_line - target_line) <= _CONTEXT_LINES:
                include = True
        else:
            if abs(current_new_line - target_line) <= _CONTEXT_LINES:
                include = True
            current_new_line += 1
        if include or i < hunk_start + 3:
            kept.append(line)
    return "\n".join(kept) if kept else diff_text


def build_change_slice(
    *,
    repository_path: str,
    path: str | None,
    line: int | None,
) -> tuple[str, str, bool]:
    """Собрать bounded change slice и статус.

    Returns:
        (change_slice_status, slice_text, change_hunk_present)
    """
    if not path:
        return "NO_PATH", "", False
    repo = Path(repository_path)
    rel = path.replace("\\", "/")
    diff_text, git_ok = _run_git_diff(repo, rel)
    if not git_ok:
        return "NO_GIT", "", False
    if not diff_text:
        return "NOT_IN_DIFF", "", False
    if rel not in diff_text and Path(rel).name not in diff_text:
        return "NOT_IN_DIFF", "", False
    narrowed = _narrow_diff_hunks(diff_text, target_line=line)
    if len(narrowed) > _MAX_SLICE_CHARS:
        narrowed = narrowed[: _MAX_SLICE_CHARS - len(_TRUNC_MARKER)] + _TRUNC_MARKER
    return "IN_DIFF", narrowed, True
