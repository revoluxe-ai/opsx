"""Идентификаторы бага: сущность, наблюдение и ссылка между слоями."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bug_id_from_fingerprint(fingerprint: str) -> str:
    """Стабильный ``BUG-`` id из fingerprint (без change_id)."""
    return f"BUG-{fingerprint[:12].upper()}"


def sighting_id_for(
    *,
    bug_id: str,
    run_id: str,
    lane: str,
    round_n: int,
    ordinal: int,
) -> str:
    """Идемпотентный id одного наблюдения бага в прогоне."""
    raw = f"{bug_id}|{run_id}|{lane}|{round_n}|{ordinal}"
    return f"SIGHT-{_sha(raw)[:16].upper()}"


def case_id_for(bug_id: str) -> str:
    """Legacy-совместимый case эпизода repair для одного бага."""
    return f"BUGCASE-{bug_id}"


@dataclass(frozen=True)
class BugRef:
    """Лёгкая ссылка, которую носят state и агенты.

    Attributes:
        bug_id: Канонический id сущности бага.
        local_id: Локальный alias (F-0001, PBH-004) в рамках прогона.
        change_id: OpenSpec change sighting или None для standalone.
    """

    bug_id: str
    local_id: str | None
    change_id: str | None
