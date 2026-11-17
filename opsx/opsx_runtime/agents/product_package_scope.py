"""Пакеты продукта из pyproject в restrict scope slice/repair hops.

Жёсткий список ``src/**`` не покрывает layout, где Poetry ``packages.include``
указывает другое дерево. Чтение такого файла на hop без discovery
(``read``/``edit``) даёт ``outside_restrict_allow`` и на втором нарушении
``TOOL_SCOPE_VIOLATION``. Служебные деревья (``opsx``, ``openspec``) не
расширяют allow: schema и runtime остаются закрытыми.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Mapping

from agents.config import ReadScope
from agents.implementation.hop_read_scope import is_slice_hop_agent_key
from agents.repair.product_surface import is_repair_product_hop

_DESIGN_KEYS = frozenset({"apply_design", "apply_design_review", "apply"})

_SKIP_ROOTS = frozenset(
    {
        "opsx",
        ".opsx",
        "openspec",
        ".cursor",
        ".git",
        ".github",
        "node_modules",
        "tools",
        ".venv",
    }
)


def _hop_merges_product_packages(agent_key: str | None) -> bool:
    key = str(agent_key or "").strip()
    return (
        is_slice_hop_agent_key(key)
        or is_repair_product_hop(key)
        or key in _DESIGN_KEYS
    )


def _safe_package_glob(include: str, from_dir: str) -> str | None:
    inc = include.replace("\\", "/").strip().strip("/")
    src = from_dir.replace("\\", "/").strip().strip("/")
    if not inc or ".." in inc.split("/") or ".." in src.split("/"):
        return None
    if inc.startswith(".env") or src.startswith(".env"):
        return None
    root = (src or inc).split("/")[0]
    if root in _SKIP_ROOTS or root.startswith("."):
        return None
    rel = f"{src}/{inc}" if src else inc
    if rel.endswith(("py", "toml", "ini")) and "." in rel.split("/")[-1]:
        return rel
    return f"{rel}/**"


def poetry_product_package_globs(repo_root: Path) -> tuple[str, ...]:
    """Глобы ``[tool.poetry] packages``; пусто, если файла или списка нет."""
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return ()
    if not isinstance(data, dict):
        return ()
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    packages = poetry.get("packages") if isinstance(poetry, dict) else None
    if not isinstance(packages, list):
        return ()
    globs: list[str] = []
    for item in packages:
        include = ""
        from_dir = ""
        if isinstance(item, str):
            include = item
        elif isinstance(item, dict):
            raw_inc = item.get("include")
            raw_from = item.get("from")
            if isinstance(raw_inc, str):
                include = raw_inc
            if isinstance(raw_from, str):
                from_dir = raw_from
        else:
            continue
        glob = _safe_package_glob(include, from_dir)
        if glob:
            globs.append(glob)
    return tuple(dict.fromkeys(globs))


def _repo_root_from_state(state: Mapping[str, Any]) -> Path | None:
    raw = str(state.get("repository_path") or state.get("workspace") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_dir():
        return None
    return root


def merge_declared_product_packages(
    scope: ReadScope,
    state: Mapping[str, Any],
    *,
    agent_key: str | None,
) -> ReadScope:
    """Добавить Poetry product packages в allow hop. Deny и чужие роли не трогает."""
    if not _hop_merges_product_packages(agent_key):
        return scope
    root = _repo_root_from_state(state)
    if root is None:
        return scope
    extra = poetry_product_package_globs(root)
    if not extra:
        return scope
    base = scope.restrict_allow or ()
    merged = tuple(dict.fromkeys((*base, *extra)))
    write = scope.restrict_write_allow
    if write is not None:
        write = tuple(dict.fromkeys((*write, *extra)))
    return ReadScope(
        allow=scope.allow,
        deny=scope.deny,
        restrict_allow=merged,
        restrict_write_allow=write,
    )
