"""Форматирование отчёта scenario-coverage audit для stdout и state."""

from __future__ import annotations

from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageVerifyAnswer


def format_scenario_coverage_report(
    *,
    change_id: str,
    answer: ScenarioCoverageVerifyAnswer,
    marker: str,
) -> str:
    """Собрать человекочитаемый отчёт audit + LOCAL_HOP_COPY_BLOCK + marker."""
    gaps_block = (
        "\n".join(
            f"- [{g.scenario_id}] {g.gap_class}: {g.evidence}" for g in answer.gaps
        )
        if answer.gaps
        else "- none"
    )
    lines = [
        "## Scenario test coverage audit",
        "mode=audit (runtime read-only)",
        "",
        f"Change: {change_id}",
        "",
        "## Итоговый вердикт",
        answer.summary,
        "",
        f"verdict: {answer.verdict}",
        "",
        "## Gaps",
        gaps_block,
        "",
    ]
    if answer.block_reason.strip():
        lines.extend(["## Block reason", answer.block_reason.strip(), ""])
    lines.extend(
        [
            "```text",
            answer.local_hop_copy_block.strip(),
            "```",
            "",
            marker,
        ],
    )
    return "\n".join(lines)
