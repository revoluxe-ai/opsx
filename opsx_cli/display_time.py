"""Форматирование UTC ISO-меток для панели OPSX (отображение по Москве)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

# MSK без DST (с 2014); не требует пакета tzdata на Windows.
_MSK = timezone(timedelta(hours=3), name="MSK")


def parse_utc_iso(value: str | None) -> datetime | None:
    """Разобрать ISO-8601 из status.json (UTC, опционально с микросекундами)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_moscow_wall_clock(value: str | datetime | None) -> str:
    """Московское время для UI: ``YYYY-MM-DD HH:MM:SS`` (без offset и микросекунд)."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = parse_utc_iso(value)
    if dt is None:
        return "" if value is None else str(value)
    return dt.astimezone(_MSK).strftime("%Y-%m-%d %H:%M:%S")


def format_run_record_times(record: Mapping[str, Any]) -> dict[str, Any]:
    """Копия записи запуска с человекочитаемыми ``started_at`` / ``finished_at``."""
    out = dict(record)
    for key in ("started_at", "finished_at"):
        raw = out.get(key)
        if isinstance(raw, str) and raw.strip():
            out[key] = format_moscow_wall_clock(raw)
    return out