"""Форматирование отчёта coverage activation."""

from __future__ import annotations

from typing import Any

from agents.verify.coverage_activation.schemas import CoverageActivationAnswer


def format_coverage_activation_report(
    *,
    change_id: str,
    answer: CoverageActivationAnswer,
    marker: str,
    metrics: dict[str, Any],
    inventory: dict[str, Any],
) -> str:
    """Текстовый отчёт hop для Owner и persist."""
    lines = [
        marker,
        f"Change: {change_id}",
        f"Verdict: {answer.verdict}",
        answer.summary,
        "",
        "Metrics:",
        f"  Changed files: {metrics.get('changed_files', 0)}",
        f"  Already covered: {metrics.get('already_covered', 0)}",
        f"  Static activated: {metrics.get('static_activated', 0)}",
        f"  Related tests found: {metrics.get('related_tests', 0)}",
        f"  Disabled related: {metrics.get('disabled_related', 0)}",
        f"  Tests reactivated: {metrics.get('tests_reactivated', 0)}",
        f"  Mypy paths registered: {metrics.get('mypy_registered', 0)}",
        f"  Blocked / unresolved: {metrics.get('blocked', 0)}",
        "",
        f"BASE_SHA: {inventory.get('base_sha', '')}",
        f"current_changed: {len(inventory.get('current_changed') or [])}",
        f"historical_touched: {len(inventory.get('historical_touched') or [])}",
    ]
    if answer.test_coverage_gaps:
        lines.append("")
        lines.append("Test coverage gaps:")
        for gap in answer.test_coverage_gaps:
            lines.append(f"  - {gap}")
    if answer.block_reason:
        lines.append("")
        lines.append(f"Block: {answer.block_reason}")
    return "\n".join(lines)
