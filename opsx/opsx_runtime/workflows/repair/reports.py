"""Typed durable reports emitted by the repair runner."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .schemas import (
    FanInPayload,
    FindingOutcome,
    HuntPayload,
    InvariantPayload,
    RelationPayload,
    RepairState,
    WorkerReceipt,
)


class StrictReport(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageReport(StrictReport):
    mission_id: str
    agent_runs: int
    known_input_tokens: int
    runs_with_unknown_input: int
    max_agent_input_tokens: int
    any_input_limit_exceeded: bool
    runs: list[WorkerReceipt]


class MissionReport(StrictReport):
    mission_id: str
    status: str
    current_stage: str
    error: str | None
    workspace: str
    isolated_workspace: bool
    finished: list[FindingOutcome]
    rejects: list[dict[str, Any]]
    invariants: list[InvariantPayload]
    hunts: list[HuntPayload]
    relations: list[RelationPayload]
    fanin: FanInPayload | None
    owner_final: str | None


def build_usage_report(state: RepairState) -> UsageReport:
    known = [
        run.input_tokens_reported
        for run in state.runs
        if run.input_tokens_reported is not None
    ]
    return UsageReport(
        mission_id=state.mission_id,
        agent_runs=len(state.runs),
        known_input_tokens=sum(known),
        runs_with_unknown_input=sum(
            run.input_tokens_reported is None for run in state.runs
        ),
        max_agent_input_tokens=state.max_agent_input_tokens,
        any_input_limit_exceeded=any(
            run.input_limit_exceeded is True for run in state.runs
        ),
        runs=state.runs,
    )


def build_mission_report(state: RepairState) -> MissionReport:
    return MissionReport(
        mission_id=state.mission_id,
        status=state.status,
        current_stage=state.current_stage,
        error=state.error,
        workspace=state.workspace,
        isolated_workspace=state.isolated_workspace,
        finished=state.finished,
        rejects=state.rejects,
        invariants=state.invariants,
        hunts=state.hunts,
        relations=state.relations,
        fanin=state.fanin,
        owner_final=state.owner_final,
    )
