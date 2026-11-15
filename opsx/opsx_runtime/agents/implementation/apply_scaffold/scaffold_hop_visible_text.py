"""Санитизация текста apply-scaffold hop: без путей и имён runtime-модулей для модели."""

from __future__ import annotations

import re
from typing import Any

# Маркеры, которые провоцируют чтение вне restrict_allow (см. regression tests).
_FORBIDDEN_SUBSTRINGS = (".py", "opsx/", "openspec/schemas")

_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:openspec|src|tests|migrations|opsx|opsx_tools|\.cursor)"
    r"/[^\s\"']+"
)
_PY_FILENAME_RE = re.compile(r"\b[\w.-]+\.py\b", re.IGNORECASE)


def sanitize_scaffold_hop_visible_text(text: str) -> str:
    """Убрать из hop-текста пути и маркеры, ведущие к forbidden reads."""
    out = str(text or "")
    out = _WINDOWS_PATH_RE.sub("[path]", out)
    out = _POSIX_PATH_RE.sub("[path]", out)
    out = _PY_FILENAME_RE.sub("[module]", out)
    return out


def scaffold_hop_visible_text_is_clean(text: str) -> bool:
    """``True``, если текст не содержит forbidden маркеров для scaffold hop."""
    lowered = str(text or "").lower()
    return not any(marker in lowered for marker in _FORBIDDEN_SUBSTRINGS)


def format_scaffold_gate_errors_for_model(errors: list[dict[str, Any]]) -> str:
    """Свести schema gate errors к полевым сообщениям без путей и имён модулей."""
    lines: list[str] = []
    for err in errors[:12]:
        if not isinstance(err, dict):
            continue
        code = str(err.get("code") or "?").strip()
        message = sanitize_scaffold_hop_visible_text(str(err.get("message") or ""))
        lines.append(f"{code}: {message}")
    if not lines:
        return "SCAFFOLD.json failed machine schema validation"
    return "\n".join(lines)
