"""Форматирование отчёта PHASE_ARCHIVE для stdout и receipt."""

from __future__ import annotations

from typing import Any

from agents.verify.archive_verify.schemas import VerifyArchiveAnswer


def format_verify_archive_report(
    *,
    change_id: str,
    answer: VerifyArchiveAnswer,
    preflight: dict[str, Any],
    marker: str,
) -> str:
    """Собрать человекочитаемый archive-отчёт + LOCAL_HOP_COPY_BLOCK + final marker."""
    gaps_block = (
        "\n".join(f"- {g}" for g in answer.gap_details)
        if answer.gap_details
        else "- none"
    )
    warnings_block = (
        "\n".join(f"- {w}" for w in answer.warnings)
        if answer.warnings
        else "- none"
    )
    missing = (preflight.get("artifact_bundle_meta") or {}).get("missing") or []
    missing_line = ", ".join(missing) if missing else "none"
    tasks = preflight.get("tasks_progress") or {}
    lines = [
        "## Verify phase",
        "mode=archive",
        "",
        f"Проверяю change: {change_id}",
        "",
        "## Итоговый вердикт",
        (
            "GO — можно переходить к /opsx-archive (при отсутствии иных blockers)."
            if answer.verdict == "GO"
            else "NO_GO — archive запрещён до устранения blockers."
        ),
        "",
        "## Completeness",
        answer.completeness_summary,
        "",
        "## Correctness",
        answer.correctness_summary,
        "",
        "## Coherence",
        answer.coherence_summary,
        "",
        "## Product Acceptance",
        f"status: {answer.product_acceptance_status}",
        f"preflight: {preflight.get('product_acceptance', {}).get('status', 'n/a')}",
        "",
        "## Контекст change",
        f"change_exists: {preflight.get('change_exists')}",
        f"missing planning files: {missing_line}",
        f"tasks: {tasks.get('completed', '?')}/{tasks.get('total', '?')} complete",
        f"channel_archive_gate: {(preflight.get('channel_archive_gate') or {}).get('status', 'n/a')}",
        "",
        "## Gaps",
        gaps_block,
        "",
        "## Warnings",
        warnings_block,
        "",
        "```text",
        answer.local_hop_copy_block.strip(),
        "```",
        "",
        marker,
    ]
    return "\n".join(lines)
