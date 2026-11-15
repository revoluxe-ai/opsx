"""Сборка JSON-пакета для prompt генерации commit message."""

from __future__ import annotations

import json
from typing import Any

from workflows.repair.schemas import RepairState


def build_commit_message_input_pack(
    state: RepairState,
    *,
    staged_diff_stat: str | None,
) -> dict[str, Any]:
    """Собрать контекст fix evidence, finding и stat staged diff для LLM.

    Args:
        state: Repair state после FIX/apply hop.
        staged_diff_stat: Вывод ``git diff --cached --stat`` (может быть пустым).

    Returns:
        Словарь для сериализации в prompt (JSON).
    """
    fix = state.fix
    finding: dict[str, Any] | None = None
    if state.active is not None:
        finding = state.active.model_dump(mode="json")

    return {
        "mission_id": state.mission_id,
        "fix_evidence": fix.model_dump(mode="json") if fix is not None else None,
        "finding": finding,
        "classification_kind": (
            state.classification.kind if state.classification is not None else None
        ),
        "commit_message_task": state.commit_message_task or "",
        "staged_diff_stat": staged_diff_stat or "",
    }


def pack_json(package: dict[str, Any]) -> str:
    """Сериализовать input pack для вставки в prompt."""
    return json.dumps(package, ensure_ascii=False, indent=2)
