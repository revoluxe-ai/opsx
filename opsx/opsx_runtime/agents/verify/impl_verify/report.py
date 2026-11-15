"""Форматирование отчёта PHASE_IMPL для stdout и receipt."""

from __future__ import annotations

from typing import Any

from agents.verify.impl_verify.schemas import VerifyImplAnswer


def format_verify_impl_report(
    *,
    change_id: str,
    answer: VerifyImplAnswer,
    preflight: dict[str, Any],
    marker: str,
) -> str:
    """Собрать человекочитаемый отчёт + LOCAL_HOP_COPY_BLOCK + final marker."""
    gaps_block = (
        "\n".join(f"- {g}" for g in answer.gap_details)
        if answer.gap_details
        else "- none"
    )
    missing = (preflight.get("artifact_bundle_meta") or {}).get("missing") or []
    missing_line = ", ".join(missing) if missing else "none"
    lines = [
        "## Verify phase",
        "mode=impl",
        "",
        f"Проверяю change: {change_id}",
        "",
        "## Итоговый вердикт",
        (
            "IMPL_OK — реализация соответствует плану change (archive ещё отдельно)."
            if answer.verdict == "IMPL_OK"
            else "IMPL_GAPS — план и реализация расходятся; нужен apply/update по gaps."
        ),
        "",
        "## Поведение",
        answer.behavior_summary,
        "",
        "## Архитектурное размещение",
        answer.architecture_placement,
        "",
        "## Согласованность tasks",
        answer.tasks_consistency,
        "",
        "## Контекст change",
        f"change_exists: {preflight.get('change_exists')}",
        f"missing planning files: {missing_line}",
        "",
        "## Gaps",
        gaps_block,
        "",
        "```text",
        answer.local_hop_copy_block.strip(),
        "```",
        "",
        marker,
    ]
    return "\n".join(lines)
