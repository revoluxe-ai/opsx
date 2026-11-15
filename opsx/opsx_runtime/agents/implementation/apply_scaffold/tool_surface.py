"""Инструменты apply-scaffold: без grep/glob/shell и без пути корня репозитория.

Поиск выполняет рантайм по каталогам ``work_scope`` из APPLY-DESIGN.
Модель получает только ``read`` и ``edit``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import fnmatch
from pathlib import Path
from typing import Any, Sequence

SCAFFOLD_TOOLS: tuple[str, ...] = ("read", "edit")
INPUT_SCOPE_INCOMPLETE = "INPUT_SCOPE_INCOMPLETE"


def work_scope_from_design(design: dict[str, Any] | None, slice_id: str) -> list[str]:
    """Пути слайса из ``scaffold_contract`` и ``expected_product_write_surfaces``.

    Пустой список значит, что дизайна не хватает и discovery репозитория запрещён.
    """
    if not isinstance(design, dict) or not slice_id.strip():
        return []
    slices = design.get("slices")
    if not isinstance(slices, list):
        return []
    paths: list[str] = []
    for raw in slices:
        if not isinstance(raw, dict) or str(raw.get("slice_id") or "") != slice_id:
            continue
        paths.extend(_paths_from_value(raw.get("expected_product_write_surfaces")))
        contract = raw.get("scaffold_contract")
        if isinstance(contract, dict):
            paths.extend(_paths_from_value(contract.get("required_surfaces")))
            paths.extend(_paths_from_value(contract.get("allowed_scaffold_writes")))
        break
    return _dedupe(paths)


def search_in_scope(
    repo_root: Path,
    roots: Sequence[str],
    pattern: str,
) -> list[str]:
    """Имена файлов под ``roots``. Аргумента path у модели нет: корни задаёт рантайм.

    Raises:
        ValueError: корень репозитория или пустой префикс попали в ``roots``.
    """
    repo = repo_root.resolve()
    found: list[str] = []
    for raw in roots:
        rel = str(raw).strip().replace("\\", "/").lstrip("/")
        if not rel or rel in {".", "./"}:
            raise ValueError("work_scope entry must be a path inside the repository")
        base = (resource_path(repo, rel)).resolve()
        if base == repo or repo not in base.parents:
            raise ValueError("work_scope must not be the repository root")
        if not base.exists():
            continue
        if base.is_file():
            if fnmatch.fnmatch(base.name, pattern):
                found.append(base.relative_to(repo).as_posix())
            continue
        for path in base.rglob("*"):
            if path.is_file() and fnmatch.fnmatch(path.name, pattern):
                found.append(path.relative_to(repo).as_posix())
    return sorted(set(found))


def _paths_from_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().replace("\\", "/"))
        elif isinstance(item, dict):
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                out.append(path.strip().replace("\\", "/"))
    return out


def _dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out
