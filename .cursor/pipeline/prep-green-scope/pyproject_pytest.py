"""Безопасное чтение/запись ``[tool.pytest.ini_options].addopts`` в ``pyproject.toml``.

APPLY не вставляет ``--deselect`` одной double-quoted строкой (ломает TOML при
кавычках в parametrize). Используем TOML-массив строк + ``tomllib`` validate.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

PYPROJECT_REL = Path("pyproject.toml")
_DESELECT_PREFIX = "--deselect="
_DESELECT_IN_STRING = re.compile(r'--deselect=(?:\"([^\"]*)\"|(\S+))')
_DEFAULT_BASE = ["--import-mode=importlib"]


def _toml_basic_string(value: str) -> str:
    """TOML basic string (JSON-escape совместим с TOML для нашего набора символов)."""

    return json.dumps(value, ensure_ascii=False)


def validate_pyproject_toml(path: Path) -> None:
    """Fail-closed: файл должен парситься ``tomllib``."""

    tomllib.load(path.open("rb"))


def _parse_addopts_raw(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        return raw.split()
    return []


def _strip_deselect_tokens(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        if tok.startswith(_DESELECT_PREFIX):
            continue
        if tok == "--deselect":
            continue
        out.append(tok)
    return out


def _deselect_flag(node_id: str) -> str:
    return f"{_DESELECT_PREFIX}{node_id}"


def read_base_addopts(root: Path) -> list[str]:
    """Токены addopts без ``--deselect`` (из валидного TOML)."""

    path = root / PYPROJECT_REL
    data = tomllib.load(path.open("rb"))
    ini = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    raw = ini.get("addopts")
    if isinstance(raw, list):
        return _strip_deselect_tokens([str(x) for x in raw])
    if isinstance(raw, str):
        cleaned = _DESELECT_IN_STRING.sub("", raw)
        return _strip_deselect_tokens(cleaned.split()) or list(_DEFAULT_BASE)
    return list(_DEFAULT_BASE)


def read_deselect_node_ids(root: Path) -> list[str]:
    """Node id из текущих ``--deselect=`` в addopts."""

    path = root / PYPROJECT_REL
    data = tomllib.load(path.open("rb"))
    ini = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    raw = ini.get("addopts")
    if isinstance(raw, list):
        nodes: list[str] = []
        for tok in raw:
            s = str(tok)
            if s.startswith(_DESELECT_PREFIX):
                nodes.append(s[len(_DESELECT_PREFIX) :])
        return nodes
    if isinstance(raw, str):
        nodes = []
        for m in _DESELECT_IN_STRING.finditer(raw):
            nodes.append(m.group(1) or m.group(2) or "")
        return [n for n in nodes if n]
    return []


def normalize_addopts_to_array(root: Path) -> bool:
    """Если ``addopts`` — одна строка, переписать в TOML-массив (без смены набора deselect)."""

    path = root / PYPROJECT_REL
    data = tomllib.load(path.open("rb"))
    ini = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    raw = ini.get("addopts")
    if isinstance(raw, list):
        return False
    base = read_base_addopts(root)
    nodes = read_deselect_node_ids(root)
    write_pytest_addopts(root, base=base, deselect_nodes=nodes)
    return True


def write_pytest_addopts(root: Path, *, base: list[str], deselect_nodes: list[str]) -> None:
    """Записать ``addopts`` как TOML-массив; после записи — ``tomllib`` validate."""

    path = root / PYPROJECT_REL
    text = path.read_text(encoding="utf-8")
    base_clean = _strip_deselect_tokens(base) or list(_DEFAULT_BASE)
    combined: list[str] = []
    seen: set[str] = set()
    for item in base_clean:
        if item not in seen:
            seen.add(item)
            combined.append(item)
    for node in deselect_nodes:
        flag = _deselect_flag(node)
        if flag not in seen:
            seen.add(flag)
            combined.append(flag)
    new_block = _format_addopts_array(combined)
    updated = _replace_addopts_in_text(text, new_block)
    path.write_text(updated, encoding="utf-8")
    validate_pyproject_toml(path)


def _format_addopts_array(items: list[str]) -> str:
    if not items:
        return "addopts = []"
    lines = ["addopts = ["]
    for item in items:
        lines.append(f"  {_toml_basic_string(item)},")
    lines.append("]")
    return "\n".join(lines)


_ADDOPTS_LINE = re.compile(r"^addopts\s*=.*$", re.MULTILINE)
_ADDOPTS_ARRAY_TAIL = re.compile(r"^\s+.*$")


def _replace_addopts_in_text(text: str, new_block: str) -> str:
    """Заменить ``addopts = …`` (одна строка или multiline array)."""

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("addopts ") or line.startswith("addopts="):
            if not replaced:
                out.append(new_block + ("\n" if not new_block.endswith("\n") else ""))
                replaced = True
            i += 1
            while i < len(lines):
                peek = lines[i]
                stripped = peek.strip()
                if stripped == "]":
                    i += 1
                    break
                if peek.startswith("  ") or peek.startswith("\t"):
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    if not replaced:
        raise RuntimeError("pyproject.toml: missing [tool.pytest.ini_options] addopts")
    return "".join(out)


def repair_invalid_pyproject_addopts(root: Path) -> bool:
    """Если TOML не парсится — сбросить addopts на безопасный массив (infra recovery)."""

    path = root / PYPROJECT_REL
    try:
        validate_pyproject_toml(path)
        return False
    except tomllib.TOMLDecodeError:
        text = path.read_text(encoding="utf-8")
        head = text.split("[tool.pytest.ini_options]", 1)
        if len(head) != 2:
            raise
        before, rest = head[0], head[1]
        after = rest.split("[tool.mypy]", 1)
        mid = after[0]
        tail = "[tool.mypy]" + after[1] if len(after) > 1 else ""
        mid_lines = [
            'testpaths = ["tests"]',
            'pythonpath = [".", "src", ".cursor/pipeline"]',
            _format_addopts_array(list(_DEFAULT_BASE)),
            'asyncio_mode = "auto"',
            'asyncio_default_fixture_loop_scope = "function"',
        ]
        if "markers = [" in mid:
            m_start = mid.index("markers = [")
            markers_block = mid[m_start:].split("\n\n", 1)[0]
            mid_lines.append(markers_block.rstrip())
        new_text = before + "[tool.pytest.ini_options]\n" + "\n".join(mid_lines) + "\n\n" + tail
        path.write_text(new_text, encoding="utf-8")
        validate_pyproject_toml(path)
        return True
