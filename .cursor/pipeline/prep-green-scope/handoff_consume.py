"""Load canonical PREP_GREEN_FULL_HANDOFF only (no Final-report fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from full_check import FullStaticCheckResult


@dataclass(frozen=True)
class HandoffConsumeError(Exception):
    """Prep cannot proceed without valid canonical handoff."""

    verdict: str
    message: str

    def __str__(self) -> str:
        return f"{self.verdict}: {self.message}"


def load_full_from_handoff(handoff: dict[str, Any] | None) -> FullStaticCheckResult:
    """Single entry: ``PREP_GREEN_FULL_HANDOFF`` → ``FullStaticCheckResult``."""

    if handoff is None or not isinstance(handoff, dict) or not handoff:
        raise HandoffConsumeError(
            "BLOCKED_MISSING_FULL_HANDOFF",
            "missing PREP_GREEN_FULL_HANDOFF payload",
        )
    try:
        return FullStaticCheckResult.from_static_check_handoff(handoff)
    except ValueError as exc:
        raise HandoffConsumeError(
            "BLOCKED_MISSING_FULL_HANDOFF",
            str(exc),
        ) from exc
