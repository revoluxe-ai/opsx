"""Collect pytest node ids for canonical /opsx-static-check wave selectors."""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

WAVE_MARKER: dict[str, str] = {
    "pytest-unit": "unit",
    "pytest-integration": "integration",
    "pytest-destructive": "destructive_local",
}

_NODE_LINE = re.compile(r"^(?P<node>\S+::\S+)\s")


@dataclass(frozen=True)
class WaveCollectResult:
    """Collect-only outcome plus deterministic marker inventory fallback."""

    nodes: tuple[str, ...]
    collect_failed: bool
    used_fallback: bool


def canonical_marker_for_check(check_id: str) -> str | None:
    return WAVE_MARKER.get(check_id)


def _uw82_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _function_has_marker(func: ast.FunctionDef | ast.AsyncFunctionDef, marker: str) -> bool:
    for dec in func.decorator_list:
        if isinstance(dec, ast.Attribute) and dec.attr == marker:
            val = dec.value
            if isinstance(val, ast.Attribute) and val.attr == "mark":
                if isinstance(val.value, ast.Name) and val.value.id == "pytest":
                    return True
        if isinstance(dec, ast.Attribute) and dec.attr == marker:
            if isinstance(dec.value, ast.Name) and dec.value.id == "pytest":
                return True
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr == marker and isinstance(dec.func.value, ast.Name):
                if dec.func.value.id == "pytest":
                    return True
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr == "mark" and isinstance(dec.func.value, ast.Attribute):
                if dec.func.value.attr == marker and isinstance(dec.func.value.value, ast.Name):
                    if dec.func.value.value.id == "pytest":
                        return True
    return False


def inventory_pytest_marked_nodes(repo_root: Path, marker: str) -> list[str]:
    """AST scan of ``tests/**`` for ``@pytest.mark.<marker>`` (no pytest subprocess)."""

    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return []
    nodes: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _function_has_marker(node, marker):
                    nid = f"{rel}::{node.name}"
                    if nid not in nodes:
                        nodes.append(nid)
    nodes.sort()
    return nodes


def _parse_collect_stdout(stdout: str) -> list[str]:
    nodes: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("="):
            continue
        m = _NODE_LINE.match(line)
        if m:
            node = m.group("node")
            if node not in nodes:
                nodes.append(node)
        elif "::" in line and not line.startswith("<"):
            if line not in nodes:
                nodes.append(line)
    nodes.sort()
    return nodes


def collect_pytest_nodes_for_wave(repo_root: Path, check_id: str) -> WaveCollectResult:
    """Run collect-only; on failure use deterministic marker inventory."""

    marker = canonical_marker_for_check(check_id)
    if marker is None:
        return WaveCollectResult(nodes=(), collect_failed=False, used_fallback=False)

    fallback = inventory_pytest_marked_nodes(repo_root, marker)
    tests_dir = repo_root / "tests"
    pyproject = repo_root / "pyproject.toml"
    if not tests_dir.is_dir() or not pyproject.is_file():
        return WaveCollectResult(
            nodes=tuple(fallback),
            collect_failed=True,
            used_fallback=bool(fallback),
        )

    cmd = [
        "poetry",
        "run",
        "pytest",
        "-c",
        str(pyproject.resolve()),
        "--rootdir",
        str(repo_root.resolve()),
        "--collect-only",
        "-q",
        "-m",
        marker,
        str(tests_dir.resolve()),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_uw82_root()),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return WaveCollectResult(
            nodes=tuple(fallback),
            collect_failed=True,
            used_fallback=bool(fallback),
        )

    if proc.returncode in {0, 5}:
        nodes = _parse_collect_stdout(proc.stdout or "")
        if nodes:
            return WaveCollectResult(nodes=tuple(nodes), collect_failed=False, used_fallback=False)
        return WaveCollectResult(
            nodes=tuple(fallback),
            collect_failed=True,
            used_fallback=bool(fallback),
        )

    return WaveCollectResult(
        nodes=tuple(fallback),
        collect_failed=True,
        used_fallback=bool(fallback),
    )


def collect_pytest_nodes_for_wave_legacy(repo_root: Path, check_id: str) -> list[str]:
    """Backward-compatible list return."""

    return list(collect_pytest_nodes_for_wave(repo_root, check_id).nodes)
