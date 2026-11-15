"""Разбор строк ``OPSX_GATE_PROGRESS`` из stdout pytest (в т.ч. склеенных с ``PASSED``)."""

from __future__ import annotations

import re
from typing import Iterator

_OPSX_GATE_SEGMENT_RE = re.compile(
    r"OPSX_GATE_PROGRESS\s+node=(?P<node>\S+)\s+when=(?P<when>setup|call|teardown)\s+"
    r"event=(?P<event>start|report)(?:\s+outcome=(?P<outcome>\S+))?"
    r"(?:\s+call_limit_s=(?P<call_limit>[\d.]+))?"
)


def iter_opsx_gate_segments(line: str) -> Iterator[dict[str, str]]:
    """Все machine-readable сегменты OPSX в одной строке stdout (может быть несколько)."""
    for match in _OPSX_GATE_SEGMENT_RE.finditer(line):
        payload = {
            "node": match.group("node"),
            "when": match.group("when"),
            "event": match.group("event"),
            "outcome": match.group("outcome") or "",
        }
        raw_limit = match.group("call_limit")
        if raw_limit:
            payload["call_limit_s"] = raw_limit
        yield payload


def first_opsx_gate_segment(line: str) -> dict[str, str] | None:
    """Первый сегмент в строке (для обратной совместимости с line-start парсерами)."""
    for segment in iter_opsx_gate_segments(line):
        return segment
    return None
