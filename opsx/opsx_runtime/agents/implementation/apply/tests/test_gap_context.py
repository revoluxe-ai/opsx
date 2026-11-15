"""Тесты выбора gap_source для ApplyAgent после verify/archive hops."""

from __future__ import annotations

from agents.implementation.apply.agent import resolve_apply_gap_context
from workflows.verify.green_loop import (
    VERIFY_APPLY_GAP_SOURCE_ARCHIVE,
    VERIFY_APPLY_GAP_SOURCE_IMPL,
    VERIFY_APPLY_GAP_SOURCE_SCENARIO,
)


def test_resolve_apply_gap_context_archive_from_hint() -> None:
    """``verify_apply_gap_source=archive_verify`` → archive payload из ``answer``."""
    answer = {"verdict": "NO_GO", "gap_details": ["TASKS_INCOMPLETE: 2 open"]}
    source, payload = resolve_apply_gap_context(
        {
            "verify_apply_gap_source": VERIFY_APPLY_GAP_SOURCE_ARCHIVE,
            "mode": "archive",
            "verdict": "NO_GO",
            "answer": answer,
        },
    )
    assert source == VERIFY_APPLY_GAP_SOURCE_ARCHIVE
    assert payload == answer


def test_resolve_apply_gap_context_archive_from_mode() -> None:
    """Archive ``NO_GO`` без hint всё равно ведёт в archive apply."""
    source, _ = resolve_apply_gap_context(
        {"mode": "archive", "verdict": "NO_GO", "answer": {"gap_details": []}},
    )
    assert source == VERIFY_APPLY_GAP_SOURCE_ARCHIVE


def test_resolve_apply_gap_context_impl_gaps() -> None:
    source, payload = resolve_apply_gap_context(
        {"verdict": "IMPL_GAPS", "answer": {"gap_details": ["x"]}},
    )
    assert source == VERIFY_APPLY_GAP_SOURCE_IMPL
    assert payload["gap_details"] == ["x"]


def test_resolve_apply_gap_context_scenario_fail() -> None:
    source, _ = resolve_apply_gap_context(
        {
            "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
            "scenario_coverage_answer": {"gaps": []},
        },
    )
    assert source == VERIFY_APPLY_GAP_SOURCE_SCENARIO
