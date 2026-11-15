"""Детерминированные fingerprint багов по lane. Без change_id в ключе."""

from __future__ import annotations

import hashlib
import re

_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_TMP = re.compile(r"(?i)(?:[A-Z]:)?[\\/](?:tmp|temp|users\\[^\\]+\\appdata)[\\/]\S+")
_LINE_NO = re.compile(r":\d+(?::\d+)?")
_WS = re.compile(r"\s+")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """Убрать адреса, временные пути и лишние пробелы."""
    cleaned = _ADDR.sub("<addr>", text or "")
    cleaned = _TMP.sub("<tmp>", cleaned)
    cleaned = _WS.sub(" ", cleaned).strip().lower()
    return cleaned[:500]


def fingerprint_pytest(observation: str) -> str:
    """Node id + тип исключения + первая строка сообщения."""
    text = observation.strip()
    node = text.split(" - ", 1)[0].strip()
    node = node.removeprefix("FAILED ").strip()
    rest = text.split(" - ", 1)[1] if " - " in text else text
    first = rest.splitlines()[0] if rest else ""
    kind = "error"
    lower = rest.lower()
    for name in ("assertionerror", "timeout", "stall", "exception"):
        if name in lower:
            kind = name
            break
    return _sha(f"pytest|{node}|{kind}|{normalize_text(first)}")


def fingerprint_static(tool: str, observation: str) -> str:
    """Файл + код правила + текст без номера строки."""
    text = _LINE_NO.sub("", observation.strip())
    return _sha(f"{tool}|{normalize_text(text)}")


def fingerprint_text(*, lane: str, location: str, text: str) -> str:
    """Текстовые findings (bugbot, hunt, owner): файл + нормализованный текст."""
    loc = _LINE_NO.sub("", location.strip().lower())
    return _sha(f"{lane}|{loc}|{normalize_text(text)}")


def fingerprint_for(
    *,
    lane: str,
    observation: str,
    location: str = "",
    tool: str | None = None,
) -> str:
    """Выбрать стратегию fingerprint по lane."""
    if lane in {"pytest", "verify_gate"} and (tool in {None, "pytest"}):
        if tool == "pytest" or "::" in observation or observation.startswith("FAILED"):
            return fingerprint_pytest(observation)
    if tool in {"pyright", "mypy"} or lane in {"pyright", "mypy"}:
        return fingerprint_static(tool or lane, observation)
    return fingerprint_text(lane=lane, location=location or observation[:160], text=observation)
