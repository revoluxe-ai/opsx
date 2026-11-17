"""Строки opsx_event для output.log панели."""

from __future__ import annotations

import json
from typing import Any


def format_opsx_event(payload: dict[str, Any]) -> str:
    """Одна JSON-строка для SSE-парсера panel.py."""
    return json.dumps({"opsx_event": payload}, ensure_ascii=False)