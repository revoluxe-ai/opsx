"""Типизированное решение о продолжении verify-subgraph после nested ``static_repair``."""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END
from pydantic import BaseModel, ConfigDict

VerifyContinuationNext = Literal["verify_prepare_main_restart", "__end__"]
"""Допустимые исходы conditional edge ``static_repair`` → restart или END."""


class VerifyContinuation(BaseModel):
    """Куда идти после выхода из вложенного static_repair в verify-subgraph.

    Поле ``next`` — имя узла LangGraph или ``END`` (``__end__``). При отсутствии
    ``verify_continuation`` в state маршрут по умолчанию — END (как раньше при
    ``verify_main_route`` без ``impl_verify``).
    """

    model_config = ConfigDict(extra="forbid")

    next: VerifyContinuationNext


def continuation_end_patch() -> dict[str, dict[str, str]]:
    """Patch state: завершить verify-subgraph без повторного ``impl_verify``."""
    return {"verify_continuation": VerifyContinuation(next=END).model_dump()}


def continuation_restart_patch() -> dict[str, dict[str, str]]:
    """Patch state: после ``verify_prepare_main_restart`` снова ``impl_verify``."""
    return {
        "verify_continuation": VerifyContinuation(
            next="verify_prepare_main_restart",
        ).model_dump(),
    }
