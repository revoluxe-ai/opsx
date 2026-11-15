"""Deterministic static-check scope inventories for baseline suppressions."""

from __future__ import annotations

import json
import re
from pathlib import Path

_PY = re.compile(r"\.py$", re.I)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("./")


def _excluded(rel: str, exclude_patterns: list[str]) -> bool:
    norm = _norm(rel)
    for pat in exclude_patterns:
        p = _norm(str(pat))
        if p.endswith("/**"):
            if norm.startswith(p[: -len("/**")]):
                return True
        elif norm == p.rstrip("/") or norm.startswith(p.rstrip("/") + "/"):
            return True
    return False


def inventory_pyright_strict_files(repo_root: Path) -> list[str]:
    """All in-scope ``.py`` files from ``pyrightconfig_strict.json`` (not already ignored)."""

    cfg_path = repo_root / "pyrightconfig_strict.json"
    if not cfg_path.is_file():
        return []
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    includes = data.get("include") or []
    ignores = {_norm(str(x)) for x in (data.get("ignore") or [])}
    excludes = [str(x) for x in (data.get("exclude") or [])]
    found: list[str] = []
    for inc in includes:
        rel_inc = _norm(str(inc))
        target = repo_root / rel_inc
        if target.is_file() and _PY.search(rel_inc):
            if rel_inc not in ignores and not _excluded(rel_inc, excludes):
                found.append(rel_inc)
            continue
        if not target.is_dir():
            continue
        for path in sorted(target.rglob("*.py")):
            rel = _norm(str(path.relative_to(repo_root)))
            if rel in ignores or _excluded(rel, excludes):
                continue
            found.append(rel)
    return sorted(set(found))


def inventory_mypy_modules(repo_root: Path) -> list[str]:
    """Modules for mypy wave: one module per pyright-scope ``.py`` file."""

    mods: list[str] = []
    for rel in inventory_pyright_strict_files(repo_root):
        p = rel
        if p.endswith(".py"):
            p = p[: -len(".py")]
        mod = p.replace("/", ".").removeprefix("src.")
        if mod and mod not in mods:
            mods.append(mod)
    return sorted(mods)
