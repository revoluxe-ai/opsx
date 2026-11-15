"""Prompt CoverageActivationAgent (семантическая неоднозначность тестов)."""

from __future__ import annotations

from typing import Any


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    inventory: dict[str, Any],
    static_audit: dict[str, Any],
    related_tests: list[dict[str, Any]],
) -> str:
    """Собрать read-only prompt с machine snapshot."""
    return f"""You are CoverageActivationAgent for OPSX verify.

Workspace: {repository_path}
Change: {change_id}

Deterministic inventory and audits are FINAL — do not recompute git diff or include/exclude.

Inventory snapshot:
{inventory}

Static audit:
{static_audit}

Related tests (candidates):
{related_tests}

Tasks:
1. Confirm which related tests are semantically tied to this change (approve_test_reactivation).
2. Confirm static paths that need activation when exclude blocks file (approve_static_paths) — never approve whole legacy directories.
3. List production paths with no related tests in test_coverage_gaps.
4. Do NOT approve destructive/live tests (unsafe=true).

Activation must enable checks for changed code: pytest (reactivate skips), pyright (include/ignore in pyrightconfig_strict.json), mypy (registry + gate targets). Verdict in JSON is for the receipt only — quality gates (pytest, pyright, mypy) always run next in static_repair.

Return one JSON object matching the schema appended after this prompt.
"""
