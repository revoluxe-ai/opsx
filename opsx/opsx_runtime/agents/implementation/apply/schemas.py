"""JSON-контракт runtime-агента apply-hop (команда ``/opsx-apply``) в verify subgraph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApplyAnswer(BaseModel):
    """Структурированный итог одного apply-hop (закрытие IMPL_GAPS из verify)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["APPLY_COMPLETE", "APPLY_PARTIAL", "BLOCKED", "NO_CHANGE"]
    tasks_summary: str = Field(min_length=1)
    gap_closure_summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    remaining_gaps: list[str] = Field(default_factory=list)
    addressed_gap_ids: list[str] = Field(default_factory=list)
    unverified_tests: list[str] = Field(default_factory=list)
    next_command: str = Field(min_length=1)
    local_hop_copy_block: str = Field(min_length=1)
