"""Map Temporal workflow outcomes to operator PASS/FAIL/BLOCKED/STOP buckets.

``ok_statuses`` in ``pipelines.json`` historically mixed product-clean success
with bounded stop outcomes (``NO_REPAIR_PROGRESS``, ``STALLED``). The operator
must only report PASS when the lane ``clean_status`` is reached.
"""

from __future__ import annotations

from typing import Any


def is_product_clean_outcome(outcome: str | None, spec: dict[str, Any]) -> bool:
    """True only when the workflow reached the lane product-clean terminal."""
    clean = spec.get("clean_status")
    return bool(clean and outcome == clean)


def resolve_operator_status(outcome: str | None, spec: dict[str, Any]) -> str:
    """PASS | FAIL | BLOCKED | STOP for Owner/operator reporting."""
    if not outcome:
        return "FAIL"
    if is_product_clean_outcome(outcome, spec):
        return "PASS"

    stop = set(spec.get("stop_statuses") or [])
    if outcome in stop:
        return "STOP"

    if outcome.startswith("BLOCKED_") or outcome in {
        "REPAIR_BLOCKED",
        "OWNER_ADJUDICATION_REQUIRED",
        "PAUSE",
        "REVIEW_BLOCKED",
        "COMMIT_MATERIALIZE_BLOCKED",
    }:
        return "BLOCKED"

    uncertain = set(spec.get("uncertain_statuses") or [])
    if outcome in uncertain:
        return "FAIL"

    fail = set(spec.get("process_fail_statuses") or [])
    if outcome in fail:
        if "BLOCKED" in outcome or outcome.endswith("_BLOCKED"):
            return "BLOCKED"
        return "FAIL"

    if outcome in {"PARTIAL_COMPLETE", "FAIL_CLOSED"}:
        return "STOP"

    return "FAIL"


def pipeline_exit_success(outcome: str | None, spec: dict[str, Any]) -> bool:
    """Shell exit 0 and adversarial ack only on product-clean success."""
    return is_product_clean_outcome(outcome, spec)
