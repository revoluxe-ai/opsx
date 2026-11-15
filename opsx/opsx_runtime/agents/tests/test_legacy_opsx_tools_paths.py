"""Контракт: в runtime/opsx_tools/commands нет живых ссылок на legacy .opsx/tools."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]

_SCAN_ROOTS = (
    _REPO / "opsx" / "opsx_runtime",
    _REPO / "opsx_tools",
    _REPO / ".cursor" / "commands",
)

_FORBIDDEN = re.compile(
    r"(?<![\w./])(?:opsx/tools|\.opsx/tools|\.opsx/registry)(?![\w./])"
)

_SKIP_SUFFIX = {".live.log", ".live.ndjson", ".ndjson"}


def _iter_source_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "node_modules", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIX:
            continue
        if "run_dialogs" in path.parts:
            continue
        if path.name == "test_legacy_opsx_tools_paths.py":
            continue
        yield path


@pytest.mark.parametrize("root", _SCAN_ROOTS)
def test_no_legacy_tool_path_literals_in_tree(root: Path) -> None:
    if not root.is_dir():
        pytest.skip(f"missing {root}")
    hits: list[str] = []
    for path in _iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits[:30])
