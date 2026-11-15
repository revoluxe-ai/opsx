"""Конечный автомат статуса бага. Недопустимый переход — ANOMALY, не drop."""

from __future__ import annotations

BugStatus = str

_NEXT: dict[str | None, set[str]] = {
    None: {"OBSERVED"},
    "OBSERVED": {
        "CLASSIFIED",
        "FALSE_POSITIVE",
        "DEFERRED",
        "OUT_OF_SCOPE",
        "ROUTED_TO_SPEC",
        "BLOCKED",
        "OBSERVED",
    },
    "CLASSIFIED": {
        "RED_CONFIRMED",
        "FALSE_POSITIVE",
        "DEFERRED",
        "OUT_OF_SCOPE",
        "ROUTED_TO_SPEC",
        "BLOCKED",
        "FIXING",
    },
    "RED_CONFIRMED": {"FIXING", "BLOCKED", "FALSE_POSITIVE"},
    "FIXING": {"GREEN_CONFIRMED", "BLOCKED", "FIXING"},
    "GREEN_CONFIRMED": {"CLOSED_FIXED", "OBSERVED"},
    "CLOSED_FIXED": {"OBSERVED"},
    "FALSE_POSITIVE": set(),
    "DEFERRED": {"OBSERVED"},
    "OUT_OF_SCOPE": set(),
    "ROUTED_TO_SPEC": set(),
    "BLOCKED": {"OBSERVED", "CLASSIFIED", "BLOCKED"},
}


def apply_status(current: str | None, proposed: str) -> tuple[str, bool]:
    """Вернуть (статус, anomaly).

    Args:
        current: Текущий статус или None для нового бага.
        proposed: Желаемый следующий статус.

    Returns:
        Пара: принятый статус и флаг аномального перехода.
    """
    allowed = _NEXT.get(current, set())
    if proposed in allowed:
        return proposed, False
    if current is None:
        return "OBSERVED", proposed != "OBSERVED"
    return current, True
