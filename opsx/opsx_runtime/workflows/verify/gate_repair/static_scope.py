"""Product scope для static/typing gates verify (pyright/mypy, typing_repair)."""

from __future__ import annotations

import json
import os


def _product_source_roots() -> tuple[str, ...]:
    raw = os.environ.get("OPSX_PRODUCT_SOURCE_ROOTS_JSON", '["src"]')
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError("PRODUCT_SOURCE_ROOTS_INVALID")
    return tuple(p.replace("\\", "/").strip("/") for p in value)
_EXCLUDED_PATH_PREFIXES = (
    "opsx/",
    "tools/",
    ".cursor/",
    "tests/",
    "openspec/",
    "migrations/",
)
_EXCLUDED_ROOT_FILES = frozenset(
    {
        "run_opsx_main.py",
        "run_verify_impl.py",
    },
)


def normalize_repo_relative_path(path: str) -> str:
    """Нормализовать путь из observation к виду ``src/stroy/...``."""
    return path.replace("\\", "/").lstrip("./")


def gate_issue_in_product_static_scope(observation: str) -> bool:
    """True, если строка gate (pyright/mypy) относится к product static scope."""
    # Import lazily: dispatcher.items imports group_error.schemas, whose package
    # initializer imports GroupErrorAgent -> findings_from_diagnostics -> here.
    # A module-level import would fail during quality-gate collection.
    from agents.verify.dispatcher.items import parse_path_line

    path, _ = parse_path_line(observation.strip())
    if not path:
        return False
    norm = normalize_repo_relative_path(path)
    if norm in _EXCLUDED_ROOT_FILES:
        return False
    for prefix in _EXCLUDED_PATH_PREFIXES:
        if norm.startswith(prefix) or norm == prefix.rstrip("/"):
            return False
    if any(norm == root or norm.startswith(root + "/") for root in _product_source_roots()):
        return True
    return False
