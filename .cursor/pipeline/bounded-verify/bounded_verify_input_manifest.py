# -*- coding: utf-8 -*-
"""Детерминированный coverage manifest для snapshot v2 (product/test inputs).

Политика (единая для git и hermetic):
- Git: union merge-base..HEAD + index/worktree deltas + untracked under capability roots.
- Hermetic (no git): все файлы под roots на диске (стабильно при неизменном дереве).
- Удалённые пути — tombstones в digest (не silent empty).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from bounded_verify_capabilities import (
    product_source_roots,
    product_test_roots,
    require_product_test_roots,
)
from bounded_verify_git import git_available, git_head, git_lines, merge_base_with_head
from bounded_verify_plan import BoundedVerifyPlanError, change_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

_TASK_PATH_RE = re.compile(
    r"(?:`|\b)((?:src|app|tests)/[^\s`']+)(?:`|\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InputManifest:
    """Отсортированные relative paths по ролям + deletion tombstones."""

    product_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    deleted_product_paths: tuple[str, ...]
    deleted_test_paths: tuple[str, ...]
    provenance_head: str | None
    all_paths: tuple[str, ...]


def _matches_prefix(rel: str, prefix: str) -> bool:
    norm = rel.replace("\\", "/")
    return norm == prefix.rstrip("/") or norm.startswith(prefix)


def _classify_path(rel: str, source_roots: tuple[str, ...], test_roots: tuple[str, ...]) -> str | None:
    norm = rel.replace("\\", "/").lstrip("./")
    for prefix in test_roots:
        if _matches_prefix(norm, prefix):
            return "test"
    for prefix in source_roots:
        if _matches_prefix(norm, prefix):
            return "product"
    return None


def _paths_from_tasks(tasks_text: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _TASK_PATH_RE.finditer(tasks_text):
        rel = match.group(1).replace("\\", "/")
        if rel not in found:
            found.append(rel)
    return tuple(sorted(found))


def _filter_classified(
    raw_paths: tuple[str, ...],
    source_roots: tuple[str, ...],
    test_roots: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    product: list[str] = []
    tests: list[str] = []
    for rel in raw_paths:
        role = _classify_path(rel, source_roots, test_roots)
        if role == "product":
            product.append(rel)
        elif role == "test":
            tests.append(rel)
    return product, tests


def _git_deleted_paths(root: Path, merge_base: str | None) -> tuple[str, ...]:
    deleted: set[str] = set()
    if merge_base:
        deleted.update(git_lines(root, ["diff", "--name-only", "--diff-filter=D", f"{merge_base}...HEAD"]))
    deleted.update(git_lines(root, ["diff", "--name-only", "--diff-filter=D"]))
    deleted.update(git_lines(root, ["diff", "--cached", "--name-only", "--diff-filter=D"]))
    return tuple(sorted(deleted))


def _git_relevant_paths(
    root: Path,
    source_roots: tuple[str, ...],
    test_roots: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    merge_base = merge_base_with_head(root)
    candidates: set[str] = set()
    if merge_base:
        candidates.update(git_lines(root, ["diff", "--name-only", f"{merge_base}...HEAD"]))
    candidates.update(git_lines(root, ["diff", "--name-only"]))
    candidates.update(git_lines(root, ["diff", "--cached", "--name-only"]))
    candidates.update(git_lines(root, ["ls-files", "-o", "--exclude-standard"]))

    deleted_all = _git_deleted_paths(root, merge_base)
    existing = sorted(p for p in candidates if p not in deleted_all)
    return tuple(existing), deleted_all


def _hermetic_paths(
    root: Path,
    source_roots: tuple[str, ...],
    test_roots: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    found: list[str] = []
    for prefix in source_roots + test_roots:
        base = root / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for file_path in sorted(base.rglob("*")):
            if file_path.is_file():
                found.append(str(file_path.relative_to(root)).replace("\\", "/"))
    return tuple(sorted(set(found))), ()


def _assert_roots_resolvable(root: Path, source_roots: tuple[str, ...], test_roots: tuple[str, ...]) -> None:
    missing: list[str] = []
    for prefix in source_roots + test_roots:
        if not (root / prefix.rstrip("/")).exists():
            missing.append(prefix)
    if missing and not git_available(root):
        raise BoundedVerifyPlanError(
            "TARGET_CAPABILITY_UNRESOLVED",
            f"capability roots missing on disk: {', '.join(missing)}",
        )


def collect_input_manifest(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> InputManifest:
    """Собирает manifest релевантных product/test paths для change snapshot."""
    source_roots = product_source_roots(root=root)
    test_roots = product_test_roots(root=root)
    require_product_test_roots(root=root)
    _assert_roots_resolvable(root, source_roots, test_roots)

    cdir = change_dir(change_id, root=root)
    if not cdir.is_dir():
        raise BoundedVerifyPlanError("CHANGE_NOT_FOUND", f"change not found: {change_id}")

    tasks_path = cdir / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""
    task_paths = _paths_from_tasks(tasks_text)

    if git_available(root):
        existing_raw, deleted_raw = _git_relevant_paths(root, source_roots, test_roots)
    else:
        existing_raw, deleted_raw = _hermetic_paths(root, source_roots, test_roots)

    existing_raw = tuple(sorted(set(existing_raw) | set(task_paths)))
    product, tests = _filter_classified(existing_raw, source_roots, test_roots)
    del_product, del_tests = _filter_classified(deleted_raw, source_roots, test_roots)

    all_paths = tuple(sorted(set(product + tests + del_product + del_tests)))
    return InputManifest(
        product_paths=tuple(sorted(product)),
        test_paths=tuple(sorted(tests)),
        deleted_product_paths=tuple(sorted(del_product)),
        deleted_test_paths=tuple(sorted(del_tests)),
        provenance_head=git_head(root),
        all_paths=all_paths,
    )


def manifest_paths_to_files(
    rel_paths: tuple[str, ...],
    *,
    root: Path,
) -> list[Path]:
    """Resolve relative manifest paths to existing files (deleted paths omitted)."""
    out: list[Path] = []
    for rel in rel_paths:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            out.append(candidate)
    return out


def role_digest_with_tombstones(
    file_paths: list[Path],
    deleted_paths: tuple[str, ...],
    *,
    root: Path,
) -> tuple[str, list[str]]:
    """Digest file bytes + deletion tombstones (path membership is identity)."""
    rel_hashes: list[str] = []
    rel_list: list[str] = []
    for path in sorted(file_paths, key=lambda p: str(p)):
        try:
            rel = str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        rel_list.append(rel)
        rel_hashes.append(f"F:{rel}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    for rel in sorted(deleted_paths):
        rel_list.append(rel)
        rel_hashes.append(f"D:{rel}")
    body = "\n".join(rel_hashes).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest() if rel_hashes else hashlib.sha256(b"").hexdigest()
    return digest, rel_list
