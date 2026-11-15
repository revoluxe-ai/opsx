"""Схема read-only классификации «пачка FAIL, один PASS» (interaction/flaky/order)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InteractionClassification = Literal[
    "STATE_POLLUTION",
    "FIXTURE",
    "LIFECYCLE",
    "SHARED_RESOURCE",
    "ORDER",
    "RACE",
    "FLAKY",
    "STALE_FINDING",
    "PRODUCT",
    "LAUNCH_DEFECT",
    "UNCLEAR",
]

RepairOwner = Literal["test_repair", "logic_bug_repair", "UNCLEAR"]

ReproRequest = Literal["none", "ordered_sequence", "concurrent"]


class TestInteractionResult(BaseModel):
    """Итог test_interaction: только классификация, без правок кода."""

    finding_id: str = Field(min_length=1)
    classification: InteractionClassification
    repair_owner: RepairOwner
    repro_request: ReproRequest = "none"
    rationale: str = Field(min_length=1)
