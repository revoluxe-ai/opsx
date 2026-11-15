"""CLI exit contract for PBH Temporal runs (``start.temporal_cli_exit_code``)."""

from __future__ import annotations

import pytest

from lanes import PROJECT_BUG_HUNT_LANE_ID
from models import REVIEW_CONTRACT_INVALID
from start import temporal_cli_exit_code


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("HUNT_CLEAN", 0),
        ("STALLED", 1),
        ("NO_REPAIR_PROGRESS", 1),
        ("MAX_ROUNDS_REACHED", 1),
        (REVIEW_CONTRACT_INVALID, 1),
        ("STALE_WORKER", 1),
    ],
)
def test_pbh_temporal_cli_exit_matrix(status: str, expected_code: int) -> None:
    assert temporal_cli_exit_code(PROJECT_BUG_HUNT_LANE_ID, {"status": status}) == expected_code


def test_bugbot_stalled_still_exits_zero_for_operator_parity() -> None:
    assert temporal_cli_exit_code("bugbot", {"status": "STALLED"}) == 0
