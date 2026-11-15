"""Fail-closed reusable checks for repair workflow evidence."""

from __future__ import annotations

from .control import family_convergence
from .schemas import GreenEvidence, RedEvidence, RepairState, TokenUsage


def red_green_identity_errors(
    red: RedEvidence,
    green: GreenEvidence,
    *,
    red_test_content_identity: str | None,
    current_test_content_identity: str | None,
) -> list[str]:
    """Сверка GREEN с RED: test_id и runtime identity тестового файла."""
    errors: list[str] = []
    if green.status != "GREEN_CONFIRMED":
        errors.append(f"GREEN_STATUS_{green.status}")
        return errors
    if green.test_id != red.test_id:
        errors.append(
            f"TEST_ID_MISMATCH:{red.test_id!r}!={green.test_id!r}"
        )
    if red_test_content_identity is None:
        return errors
    if (
        current_test_content_identity != red_test_content_identity
        and not green.non_weaken_delta
    ):
        errors.append("TEST_HASH_CHANGED_WITHOUT_NON_WEAKEN_DELTA")
    return errors


def agent_input_limit_error(
    usage: TokenUsage | None,
    limit: int,
) -> str | None:
    if usage is None:
        return None
    observed = usage.observed_input_tokens()
    if observed is None:
        return None
    if observed > limit:
        return f"AGENT_INPUT_LIMIT_EXCEEDED:{observed}>{limit}"
    return None


def family_convergence_errors(state: RepairState) -> list[str]:
    _, errors = family_convergence(state)
    return errors
