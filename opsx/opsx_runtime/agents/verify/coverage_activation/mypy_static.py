"""Активация mypy для путей change (production + reactivated tests)."""

from __future__ import annotations

from opsx.project_context import resource_path

import configparser
import re
from pathlib import Path
from typing import Any

_REGISTRY_REL = Path("opsx/project/coverage_activation_mypy_paths.txt")


def mypy_registry_path(repo_root: Path) -> Path:
    """Файл дополнительных путей mypy для gate_repair (явные CLI targets)."""
    return resource_path(repo_root, _REGISTRY_REL)


def load_registered_mypy_paths(repo_root: Path) -> list[str]:
    """Прочитать зарегистрированные относительные пути (posix)."""
    path = mypy_registry_path(repo_root)
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        rel = raw.strip().replace("\\", "/").lstrip("./")
        if rel and not rel.startswith("#"):
            lines.append(rel)
    return sorted(set(lines))


def register_mypy_paths(repo_root: Path, paths: list[str]) -> dict[str, Any]:
    """Добавить пути в registry (additive, dedupe)."""
    normalized = sorted(
        {p.replace("\\", "/").lstrip("./") for p in paths if p and p.strip()},
    )
    if not normalized:
        return {"registered": [], "added": [], "registry_path": str(_REGISTRY_REL)}
    registry = mypy_registry_path(repo_root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    existing = set(load_registered_mypy_paths(repo_root))
    added = [p for p in normalized if p not in existing]
    merged = sorted(existing | set(normalized))
    registry.write_text("\n".join(merged) + "\n", encoding="utf-8")
    return {
        "registered": merged,
        "added": added,
        "registry_path": str(_REGISTRY_REL),
    }


def _load_mypy_ini(repo_root: Path) -> tuple[Path | None, configparser.ConfigParser]:
    parser = configparser.ConfigParser()
    for name in ("mypy.ini", ".mypy.ini"):
        path = resource_path(repo_root, name)
        if path.is_file():
            parser.read(path, encoding="utf-8")
            return path, parser
    return None, parser


def _mypy_exclude_pattern(repo_root: Path) -> re.Pattern[str] | None:
    _, parser = _load_mypy_ini(repo_root)
    if not parser.has_section("mypy"):
        return None
    raw = str(parser.get("mypy", "exclude", fallback="") or "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error:
        return None


def mypy_path_in_product_scope(repo_root: Path, rel_path: str) -> bool:
    """True, если путь попадает под ``files`` и не отсекается ``exclude`` в mypy.ini."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    _, parser = _load_mypy_ini(repo_root)
    if not parser.has_section("mypy"):
        return False
    files_raw = str(parser.get("mypy", "files", fallback="") or "")
    file_roots = [f.strip().replace("\\", "/").lstrip("./") for f in files_raw.split(",") if f.strip()]
    if not any(norm == root or norm.startswith(root + "/") for root in file_roots):
        return False
    pattern = _mypy_exclude_pattern(repo_root)
    if pattern is not None and pattern.search(norm):
        return False
    return True


def audit_mypy_paths(repo_root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
    """Какие пути уже в scope mypy gate, какие нужно зарегистрировать явно."""
    rows: list[dict[str, str]] = []
    in_scope = 0
    need_register = 0
    for path in paths:
        norm = path.replace("\\", "/").lstrip("./")
        if mypy_path_in_product_scope(repo_root, norm):
            rows.append({"path": norm, "disposition": "ALREADY_IN_SCOPE", "detail": "mypy.ini files+exclude"})
            in_scope += 1
        else:
            rows.append(
                {
                    "path": norm,
                    "disposition": "NEEDS_REGISTER",
                    "detail": "outside mypy files or blocked by exclude (typical tests/)",
                },
            )
            need_register += 1
    return {
        "STATUS": "READY",
        "checker": "mypy",
        "rows": rows,
        "already_in_scope_count": in_scope,
        "need_register_count": need_register,
    }


def build_mypy_activation_plan(audit: dict[str, Any]) -> dict[str, Any]:
    """Пути для ``register_mypy_paths`` (reactivated tests и вне-scope production)."""
    rows = audit.get("rows")
    if not isinstance(rows, list):
        return {"register": []}
    register: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("disposition") == "NEEDS_REGISTER":
            register.append(str(row.get("path") or ""))
    return {"register": sorted({p for p in register if p})}
