"""Детерминированный discoverability для CLASSIFIER (opsx §21.3).

Runtime собирает список релевантных путей в репозитории и передаёт их в
INPUT CONTEXT промпта, чтобы модель не угадывала пути через единственный tool `read`.
"""

from __future__ import annotations

from opsx.project_context import resource_path

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.repair.product_surface import repair_product_read_globs
from workflows.repair.schemas import RepairState

_MIGRATION_MARKERS = (
    "migrat",
    "alembic",
    "revision",
    "versions",
    "миграц",
)


def _finding_text(finding: Mapping[str, Any]) -> str:
    parts = (
        str(finding.get("finding") or ""),
        str(finding.get("location") or ""),
    )
    return " ".join(parts).lower()


def _mentions_migrations(finding: Mapping[str, Any]) -> bool:
    text = _finding_text(finding)
    return any(marker in text for marker in _MIGRATION_MARKERS)


def _relative_file(repo: Path, path: Path) -> str | None:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return None


def _collect_under(
    repo: Path,
    rel_dir: str,
    *,
    suffixes: tuple[str, ...],
    max_files: int,
    acc: list[str],
) -> None:
    """Добавить файлы под ``rel_dir`` с заданными суффиксами (порядок sorted)."""
    if len(acc) >= max_files:
        return
    root = resource_path(repo, rel_dir)
    if root.is_file() and root.suffix in suffixes:
        rel = _relative_file(repo, root)
        if rel is not None:
            acc.append(rel)
        return
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if len(acc) >= max_files:
            break
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel = _relative_file(repo, path)
        if rel is not None:
            acc.append(rel)


def _collect_migration_tests(repo: Path, *, max_files: int, acc: list[str]) -> None:
    """Файлы tests с migration/alembic в имени (ограниченный обход)."""
    tests = resource_path(repo, "tests")
    if not tests.is_dir():
        return
    for path in sorted(tests.rglob("*.py")):
        if len(acc) >= max_files:
            break
        name = path.name.lower()
        if "migration" not in name and "alembic" not in name:
            continue
        rel = _relative_file(repo, path)
        if rel is not None:
            acc.append(rel)


def build_classifier_discoverability(
    repository_path: str | Path,
    *,
    finding: Mapping[str, Any],
    max_files: int = 150,
) -> dict[str, Any]:
    """Собрать блок discoverability для INPUT CONTEXT CLASSIFIER.

    Args:
        repository_path: Корень репозитория (как в RepairState).
        finding: Активная находка (finding/location/evidence_paths).
        max_files: Верхняя граница числа путей в source_ids.

    Returns:
        Словарь с policy, source_ids, priority_reads (относительные POSIX-пути).
    """
    repo = Path(repository_path).resolve()
    source_ids: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        token = path.replace("\\", "/").strip()
        if not token or token in seen:
            return
        seen.add(token)
        source_ids.append(token)

    for raw in finding.get("evidence_paths") or []:
        _add(str(raw))

    migration_focus = _mentions_migrations(finding)
    if migration_focus or (resource_path(repo, "migrations")).exists():
        scratch: list[str] = []
        scan_roots = [
            p.rstrip("/**").rstrip("*")
            for p in repair_product_read_globs()
            if p.startswith("migrations") or p == "alembic.ini"
        ]
        for rel in (
            *scan_roots,
            "migrations/script.py.mako",
        ):
            _collect_under(
                repo,
                rel,
                suffixes=(".py", ".mako", ".ini", ".sql"),
                max_files=max_files,
                acc=scratch,
            )
        _collect_migration_tests(repo, max_files=max_files, acc=scratch)
        for path in scratch:
            _add(path)
        _add("pyproject.toml")

    priority_reads: list[str] = []
    versions = [p for p in source_ids if p.startswith("migrations/versions/") and p.endswith(".py")]
    for rel in sorted(versions, reverse=True)[:8]:
        priority_reads.append(rel)

    for rel in ("pyproject.toml", "migrations/env.py", "alembic.ini"):
        if rel in seen and rel not in priority_reads:
            priority_reads.append(rel)

    policy = (
        "OPSX §21.3 Discoverability: use only paths from source_ids or "
        "priority_reads via read; do not guess directory listings or invent paths."
    )
    if not source_ids:
        policy += " No deterministic paths were enumerated for this finding."

    return {
        "policy": policy,
        "repository_relative": True,
        "migration_focus": migration_focus,
        "source_ids": source_ids[:max_files],
        "priority_reads": priority_reads[: min(12, len(priority_reads))],
    }


def enrich_classifier_payload(
    state: RepairState,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Дополнить payload CLASSIFIER блоком discoverability перед сборкой промпта."""
    finding = payload.get("finding")
    if not isinstance(finding, dict):
        return payload
    repo = state.repository_path or state.workspace
    disc = build_classifier_discoverability(repo, finding=finding)
    merged = dict(payload)
    merged["discoverability"] = disc
    return merged