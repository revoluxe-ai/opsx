"""Runtime-встройка текстов файлов в SDK prompt (модель не открывает пути сама)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_bounded_text(path: Path, *, max_chars: int | None = None) -> str | None:
    """Прочитать файл целиком для вставки в промпт.

    ``max_chars`` оставлен для старых вызовов и текст не режет.
    """
    _ = max_chars
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def embed_source_window(
    path: Path,
    line: int | None,
    *,
    radius: int = 40,
    max_chars: int = 8_000,
) -> str | None:
    """Фрагмент исходника вокруг строки (1-based)."""
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return read_bounded_text(path, max_chars=max_chars)
    if line is None or line < 1:
        return read_bounded_text(path, max_chars=max_chars)
    idx = line - 1
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    _ = max_chars
    return "\n".join(f"{start + i + 1}: {lines[start + i]}" for i in range(end - start))


def bounded_json_block(data: Any, *, max_chars: int | None = None) -> str:
    """JSON для промпта целиком. ``max_chars`` не режет текст."""
    _ = max_chars
    return json.dumps(data, ensure_ascii=False, indent=2)
