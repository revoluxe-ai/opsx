# -*- coding: utf-8 -*-
"""Коды ошибок и PAUSE для causal history append/recovery."""

from __future__ import annotations

from enum import Enum


class HistoryErrorCode(str, Enum):
    """Детерминированные коды остановки append/recovery."""

    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    EVENT_ID_COLLISION = "EVENT_ID_COLLISION"
    OPERATION_ID_PAYLOAD_COLLISION = "OPERATION_ID_PAYLOAD_COLLISION"
    ACK_IDENTITY_COLLISION = "ACK_IDENTITY_COLLISION"
    SEQUENCE_ALLOCATION_COLLISION = "SEQUENCE_ALLOCATION_COLLISION"
    REPLAY_ORDER_BROKEN = "REPLAY_ORDER_BROKEN"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    AMBIGUOUS_TMP = "AMBIGUOUS_TMP"
    STAGNANT_CAUSAL_FAILURE = "STAGNANT_CAUSAL_FAILURE"


class HistoryPause(Exception):
    """Fail-closed PAUSE; не откатывает verified worker результат."""

    def __init__(self, code: HistoryErrorCode, detail: str, recovery: str) -> None:
        self.code = code
        self.detail = detail
        self.recovery = recovery
        super().__init__(f"{code.value}: {detail}")
