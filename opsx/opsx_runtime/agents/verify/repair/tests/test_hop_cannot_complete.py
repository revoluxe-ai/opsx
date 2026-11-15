"""``CANNOT_COMPLETE`` repair hop → ``VerifyStageStop``, FAILED repair — нет."""

from __future__ import annotations

import pytest

from agents.verify.repair.hop_cannot_complete import (
    STOP_CODE,
    raise_if_repair_hop_cannot_complete,
)
from workflows.stop_errors import VerifyStageStop


def test_cannot_complete_status_raises_verify_stage_stop() -> None:
    with pytest.raises(VerifyStageStop) as err:
        raise_if_repair_hop_cannot_complete(
            hop="test_repair",
            finding_id="F-0001",
            status="CANNOT_COMPLETE",
            summary="no tool to run pytest",
        )
    assert err.value.code == STOP_CODE
    assert "F-0001" in err.value.evidence
    assert "test_repair" in err.value.evidence


def test_failed_repair_does_not_raise_cannot_complete_stop() -> None:
    raise_if_repair_hop_cannot_complete(
        hop="test_repair",
        finding_id="F-0002",
        status="FAILED",
        summary="pytest still red after edit",
    )
