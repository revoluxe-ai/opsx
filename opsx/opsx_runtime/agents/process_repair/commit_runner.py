"""Детерминированный commit по одной записи журнала супервизора."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from agents.process_repair.schemas import CommitJournalResult

_PROTECTED_BRANCHES = frozenset({"main", "master", "prod", "production"})
_CONFLICT_MARKERS = re.compile(r"^(<<<<<<<|=======|>>>>>>>)")


def _read_journal_entry(journal_path: Path, entry_id: str) -> dict:
    """Прочитать запись журнала супервизора (``journal.sqlite``; legacy ndjson — только чтение)."""
    if journal_path.suffix in {".sqlite", ".db"} or journal_path.name == "journal.sqlite":
        if not journal_path.is_file():
            return {}
        import sqlite3

        conn = sqlite3.connect(str(journal_path), timeout=30)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT payload_json FROM journal_events "
                "WHERE json_extract(payload_json, '$.entry_id') = ? "
                "ORDER BY id DESC LIMIT 1",
                (entry_id,),
            ).fetchone()
            if row:
                parsed = json.loads(row[0])
                if isinstance(parsed, dict):
                    return parsed
        finally:
            conn.close()
    elif journal_path.is_file():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("entry_id") == entry_id:
                return row
    sidecar = journal_path.parent / "entries" / f"{entry_id}.json"
    if sidecar.is_file():
        return json.loads(sidecar.read_text(encoding="utf-8"))
    return {}


def _conflict_scan(paths: list[Path]) -> str | None:
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if _CONFLICT_MARKERS.match(line):
                return f"conflict marker in {path}"
    return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def _files_from_fix_report_scope(entry: dict) -> list[dict] | None:
    """Сузить ``files_changed`` до путей, объявленных в ``fix_report.files_changed``."""
    fix_report = entry.get("fix_report") or {}
    declared = fix_report.get("files_changed") or []
    if not declared:
        return None
    allowed: set[tuple[str, str]] = set()
    for raw in declared:
        text = str(raw).replace("\\", "/").strip()
        if not text:
            continue
        if text.startswith("opsx/core/"):
            allowed.add(("project", text))
            allowed.add(("core", text.removeprefix("opsx/core/")))
        else:
            allowed.add(("core", text))
            allowed.add(("project", f"opsx/core/{text}"))
    scoped: list[dict] = []
    for item in entry.get("files_changed") or []:
        if not isinstance(item, dict):
            continue
        repo = str(item.get("repo") or "")
        path = str(item.get("path") or "").replace("\\", "/")
        if (repo, path) in allowed:
            scoped.append(item)
    return scoped if scoped else None


def commit_journal_entry(
    *,
    project_root: Path,
    core_root: Path,
    journal_path: Path,
    entry_id: str,
    commit_message: str | None = None,
) -> CommitJournalResult:
    """Закоммитить только файлы из записи журнала (без push)."""
    entry = _read_journal_entry(journal_path, entry_id)
    files = _files_from_fix_report_scope(entry) or entry.get("files_changed") or []
    if not files:
        return CommitJournalResult(status="FAILED", error="journal entry has no files_changed")
    by_repo: dict[str, list[str]] = {"project": [], "core": []}
    for item in files:
        if not isinstance(item, dict):
            continue
        repo = str(item.get("repo") or "")
        rel = str(item.get("path") or "").replace("\\", "/")
        if repo in by_repo and rel:
            by_repo[repo].append(rel)
    messages: list[str] = []
    last_sha: str | None = None
    for repo_name, paths in by_repo.items():
        if not paths:
            continue
        root = project_root if repo_name == "project" else core_root
        branch = _git(root, "branch", "--show-current").stdout.strip()
        if branch in _PROTECTED_BRANCHES:
            return CommitJournalResult(status="BLOCKED", error=f"protected branch: {branch}")
        if _git(root, "ls-files", "-u").stdout.strip():
            return CommitJournalResult(status="BLOCKED", error="unmerged index entries")
        abs_paths = [root / rel for rel in paths]
        expected_by_path = {
            str(item.get("path") or "").replace("\\", "/"): str(item.get("sha256") or "")
            for item in files
            if isinstance(item, dict) and str(item.get("repo") or "") == repo_name
        }
        for rel, abs_path in zip(paths, abs_paths, strict=True):
            expected = expected_by_path.get(rel, "")
            if not expected:
                return CommitJournalResult(status="BLOCKED", error=f"missing sha256 for {rel}")
            if not abs_path.is_file():
                return CommitJournalResult(status="BLOCKED", error=f"file missing: {rel}")
            actual = hashlib.sha256(abs_path.read_bytes()).hexdigest()
            if actual != expected:
                return CommitJournalResult(status="BLOCKED", error=f"sha256 mismatch for {rel}")
        conflict = _conflict_scan(abs_paths)
        if conflict:
            return CommitJournalResult(status="BLOCKED", error=conflict)
        staged = _git(root, "diff", "--cached", "--name-only").stdout.strip()
        if staged:
            return CommitJournalResult(status="BLOCKED", error="foreign staged files present")
        for rel in paths:
            add = _git(root, "add", "--", rel)
            if add.returncode != 0:
                return CommitJournalResult(status="FAILED", error=add.stderr or add.stdout)
        staged_after = {
            line.replace("\\", "/")
            for line in _git(root, "diff", "--cached", "--name-only").stdout.splitlines()
            if line.strip()
        }
        expected_paths = {p.replace("\\", "/") for p in paths}
        if staged_after != expected_paths:
            _ = _git(root, "reset", "HEAD")
            extra = sorted(staged_after - expected_paths)
            missing = sorted(expected_paths - staged_after)
            return CommitJournalResult(
                status="BLOCKED",
                error=(
                    "staged paths must match journal files_changed only; "
                    f"extra={extra[:8]} missing={missing[:8]}"
                ),
            )
        subject = commit_message or f"fix(opsx): supervisor META repair ({entry_id[:8]})"
        body = (
            f"OPSX-Supervisor-Entry: {entry_id}\n\n"
            "What:\n- supervisor journal fix entry\n\n"
            "Why:\n- automated META repair after workflow failure\n"
        )
        commit = _git(root, "commit", "-m", subject, "-m", body)
        if commit.returncode != 0:
            return CommitJournalResult(status="FAILED", error=commit.stderr or commit.stdout)
        sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        last_sha = sha
        messages.append(f"{repo_name}:{sha}")
    if not last_sha:
        return CommitJournalResult(status="FAILED", error="nothing committed")
    return CommitJournalResult(status="COMMITTED", commit_sha=last_sha)