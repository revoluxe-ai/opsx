"""Зелёный skip/resume hop coverage_activation относительно apply commit."""

from __future__ import annotations

from typing import Any, Mapping

from agents.state import GraphState


def patch_coverage_activation_apply_code_changed() -> dict[str, Any]:
    """Patch после apply→commit с реальными файлами: сбросить кэшированный зелёный skip."""
    return {"coverage_activation_apply_code_changed": True}


def coverage_activation_apply_invalidates_green(state: Mapping[str, Any]) -> bool:
    """True, если перед hop apply закоммитил код и coverage_activation нельзя пропускать."""
    return state.get("coverage_activation_apply_code_changed") is True


def coverage_activation_may_skip_green_resume(
    state: Mapping[str, Any],
    *,
    prior_complete: bool,
    prior_fingerprint: str,
    fingerprint: str,
) -> bool:
    """True, если hop можно пропустить: зелёный complete, тот же отпечаток, apply не менял код."""
    if not prior_complete or prior_fingerprint != fingerprint:
        return False
    if coverage_activation_apply_invalidates_green(state):
        return False
    return True


def coverage_activation_complete_after_hop(
    *,
    verdict: str,
    skipped: bool,
    prior_complete: bool,
) -> bool:
    """True после полного прогона hop (для green skip); verdict не блокирует pipeline."""
    _ = verdict
    if skipped:
        return prior_complete
    return True


def patch_clear_coverage_activation_apply_code_changed() -> dict[str, Any]:
    """Снять флаг apply-инвалидации после полного прогона hop (не skip)."""
    return {"coverage_activation_apply_code_changed": False}
