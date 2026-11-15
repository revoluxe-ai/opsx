"""Статическая граница: общий workflow и агенты без прямой привязки к пакету провайдера.

Сканирует ``opsx_runtime/workflows`` и ``opsx_runtime/agents``, кроме
``agents/adapters/cursor_sdk`` (реализация адаптера провайдера). Тест читает
исходники (AST + текст), не доверяет комментариям о соответствии.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

_OPSX_RUNTIME = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = (
    _OPSX_RUNTIME / "workflows",
    _OPSX_RUNTIME / "agents",
    _OPSX_RUNTIME / "opsx_recording",
    _OPSX_RUNTIME / "tests",
    _OPSX_RUNTIME / "ui",
)

# Composition-root CLIs и bootstrap вне деревьев workflows/agents.
_ENTRYPOINT_FILES = (
    _OPSX_RUNTIME / "runtime_bootstrap.py",
    _OPSX_RUNTIME / "run.py",
    _OPSX_RUNTIME / "run_one_node.py",
    _OPSX_RUNTIME / "reconcile_one_node.py",
    _OPSX_RUNTIME / "run_verify_impl.py",
    _OPSX_RUNTIME / "run_implementation.py",
    _OPSX_RUNTIME / "run_verify_deterministic_smoke.py",
    _OPSX_RUNTIME / "scripts" / "ok_probe_health_check.py",
    _OPSX_RUNTIME / "scripts" / "monitor_cursor_run.py",
    _OPSX_RUNTIME / "scripts" / "export_run_conversation.py",
    _OPSX_RUNTIME / "workflows" / "repair" / "preflight.py",
    _OPSX_RUNTIME / "run_dialogs.py",
)

_SKIP_DIR_MARKERS = frozenset(
    {
        "__pycache__",
        ".git",
        "node_modules",
        "run_dialogs",
    }
)

_FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"(?i)\bCursor\s+SDK\b"),
    re.compile(r"(?i)\bCursor\s+Python\s+SDK\b"),
    re.compile(r"(?i)\bpre-?SDK\b"),
    re.compile(r"(?<![\w.])\bcursor_sdk\b(?!\w)"),
)

_CONCAT_PROVIDER_MODULE_PATTERNS = (
    re.compile(r"""["']cursor["']\s*\+\s*["'][^"']*_sdk[^"']*["']"""),
    re.compile(r"""["']cursor["']\s*\+\s*["']sdk["']"""),
)

# Модули со stem sdk_* в workflow — утечка имени провайдера (должны быть adapter-neutral).
_FORBIDDEN_MODULE_STEMS = frozenset(
    {
        "sdk_run_reconcile",
        "sdk_hop_pause",
        "sdk_store_context",
        "cursor_adapter",
    }
)

_FORBIDDEN_IMPORT_MARKERS = (
    "agents.adapters.cursor_sdk",
    "agents.adapters.cursor_sdk_adapter",
    "agents.adapters.cursor_sdk_stream",
    "agents.adapters.cursor_bridge_runtime",
    "agents.adapters.cursor_sdk_stream_minimal",
)

_ADAPTER_PROVIDER_SUBPACKAGE = "agents.adapters.cursor_sdk"


def _under_adapter_provider_zone(path: Path) -> bool:
    rel = path.relative_to(_OPSX_RUNTIME)
    parts = rel.parts
    return (
        len(parts) >= 3
        and parts[0] == "agents"
        and parts[1] == "adapters"
        and parts[2] == "cursor_sdk"
    )


def _module_pierces_adapter_subpackage(mod: str) -> bool:
    if mod == _ADAPTER_PROVIDER_SUBPACKAGE or mod.startswith(
        f"{_ADAPTER_PROVIDER_SUBPACKAGE}."
    ):
        return True
    if mod == "cursor_sdk" or mod.startswith("cursor_sdk."):
        return True
    return any(marker in mod for marker in _FORBIDDEN_IMPORT_MARKERS)


def _iter_python_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIR_MARKERS for part in path.parts):
            continue
        if _under_adapter_provider_zone(path):
            continue
        if path.name == "test_general_adapter_provider_boundary.py":
            continue
        yield path


def _forbidden_provider_imports(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if _module_pierces_adapter_subpackage(mod):
                    hits.append(f"import {mod}")
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            mod = node.module
            if _module_pierces_adapter_subpackage(mod):
                hits.append(f"from {mod}")
    return hits


def _collect_scan_paths() -> list[Path]:
    paths: list[Path] = []
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            pytest.skip(f"missing scan root {root}")
        paths.extend(_iter_python_files(root))
    for entrypoint in _ENTRYPOINT_FILES:
        if entrypoint.is_file():
            paths.append(entrypoint)
    return paths


def test_workflows_and_agents_do_not_import_cursor_sdk_package() -> None:
    """``workflows`` и ``agents`` (кроме пакета адаптера) не импортируют провайдер напрямую."""
    violations: list[str] = []
    for path in _collect_scan_paths():
        rel = path.relative_to(_OPSX_RUNTIME)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            violations.append(f"{rel}: parse error: {exc}")
            continue
        for hit in _forbidden_provider_imports(tree):
            violations.append(f"{rel}: {hit}")
    assert not violations, "\n".join(violations[:80])


def test_general_modules_avoid_provider_product_names() -> None:
    """Workflow и agents вне зоны адаптера не содержат имён продукта провайдера."""
    violations: list[str] = []
    for path in _collect_scan_paths():
        rel = path.relative_to(_OPSX_RUNTIME)
        if path.stem in _FORBIDDEN_MODULE_STEMS:
            violations.append(f"{rel}: forbidden module name {path.stem}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{rel}: read error: {exc}")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in _FORBIDDEN_TEXT_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                    break
            for pattern in _CONCAT_PROVIDER_MODULE_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        f"{rel}:{lineno}: forbidden provider module concatenation"
                    )
                    break
    assert not violations, "\n".join(violations[:80])
