"""Реплика ``ux_contract.simple_cta_transitions`` на разобранном JSON контракте."""

from __future__ import annotations

import re
from typing import Any

_SIMPLE_TARGET = re.compile(r"^[a-zA-Z][\w]*$")


def simple_cta_transition_keys(contract: dict[str, Any]) -> set[tuple[str, str]]:
    """Ключи ``(state_id, trigger_label)`` для ``CTA_TRANSITIONS`` @ BASE_SHA.

    Первое однозначное simple-target объявление в contract выигрывает (как в runtime).
    """

    out: set[tuple[str, str]] = set()
    states = contract["states"]
    for state_key in sorted(states.keys()):
        meta = states[state_key]
        if not isinstance(meta, dict):
            continue
        for tr in meta.get("transitions") or []:
            if not isinstance(tr, dict):
                continue
            trigger = str(tr.get("trigger") or "").strip()
            target_raw = str(tr.get("target") or "").strip()
            if not trigger or trigger in {"stale callback", "/start"}:
                continue
            if not _SIMPLE_TARGET.match(target_raw):
                continue
            key = (state_key, trigger)
            if key not in out:
                out.add(key)
    return out
