"""Семантические trigger-ы контракта, маршрутизируемые handler до catalog CTA."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class HandlerSemantic:
    """Описание handler ingress для не-каталожного trigger."""

    ref: str
    conditional: bool
    note: str


_EXACT_HANDLER_TRIGGERS: dict[str, HandlerSemantic] = {
    "stale callback": HandlerSemantic(
        ref="handler._parse_callback:STALE_CALLBACK",
        conditional=False,
        note="Epoch / decode policy; no FSM transition",
    ),
    "/start": HandlerSemantic(
        ref="handler._parse_event:FunnelEventKind.START",
        conditional=True,
        note="START routing priority depends on snapshot guards",
    ),
    "select date": HandlerSemantic(
        ref="handler._parse_callback:CallbackAction.PICK_DAY",
        conditional=True,
        note="INLINE_CALLBACK → FSM slot handlers",
    ),
    "select slot": HandlerSemantic(
        ref="handler._parse_callback:CallbackAction.PICK_TIME",
        conditional=True,
        note="INLINE_CALLBACK → FSM slot handlers",
    ),
    "contact": HandlerSemantic(
        ref="handler._parse_event:FunnelEventKind.PHONE_VALIDATED",
        conditional=True,
        note="Telegram contact message on phone steps",
    ),
    "confirm": HandlerSemantic(
        ref="handler._parse_callback:CallbackAction.CONFIRM_BOOK",
        conditional=True,
        note="CONFIRM_YES idempotency key; not catalog CTA index",
    ),
    "input": HandlerSemantic(
        ref="handler._parse_event:FunnelEventKind.TEXT",
        conditional=True,
        note="Free-text capture (interval, other goal, etc.)",
    ),
    "new booking/slot guard": HandlerSemantic(
        ref="handler:booking_slot_guard_pseudo",
        conditional=True,
        note="Contract guard pseudo-trigger; runtime via booking sync not single CTA",
    ),
}

_PREFIX_HANDLER_PATTERNS: list[tuple[re.Pattern[str], HandlerSemantic]] = [
    (
        re.compile(r"^select/"),
        HandlerSemantic(
            ref="handler._parse_callback:INLINE_or_TEXT",
            conditional=True,
            note="Composite contract trigger; slot/text ingress",
        ),
    ),
]


def handler_semantic_for_trigger(trigger: str) -> HandlerSemantic | None:
    """Вернуть handler semantic, если trigger не является catalog CTA label path."""

    if trigger in _EXACT_HANDLER_TRIGGERS:
        return _EXACT_HANDLER_TRIGGERS[trigger]
    for pattern, semantic in _PREFIX_HANDLER_PATTERNS:
        if pattern.search(trigger):
            return semantic
    if trigger == "supported platform":
        return HandlerSemantic(
            ref="handler._parse_callback:CallbackAction.CTA_or_GOAL",
            conditional=True,
            note="Platform list from catalog; may use CTA index after decode",
        )
    return None
