"""Synchronous, single-line terminal progress for scenario coverage workers.

Progress is operational telemetry, not PASS evidence. This intentionally uses
stderr directly: OPSX users see the queue even when Python INFO logging is
disabled. Each call emits one complete line (no progress-bar rewrite or polling).
"""
from __future__ import annotations

import json
import sys
from typing import Any


def report(event: str, /, **fields: Any) -> None:
    if not event.isupper() or not event.replace("_", "").isalpha():
        raise ValueError("SCENARIO_PROGRESS_EVENT_INVALID")
    parts = [f"[OPSX scenario-coverage] {event}"]
    for key, value in fields.items():
        if not key.isidentifier():
            raise ValueError("SCENARIO_PROGRESS_FIELD_INVALID")
        parts.append(
            f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        )
    sys.stderr.write(" ".join(parts) + "\n")
    sys.stderr.flush()
