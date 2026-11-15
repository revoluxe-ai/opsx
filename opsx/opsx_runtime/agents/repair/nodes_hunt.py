"""Hunt and fan-in performer agents.

These classes execute the invariant-hunt side of the repair system while the
workflow graph handles queueing, fan-out, and final orchestration.
"""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import interrupt

from agents.adapters.abc_adapter import ABCAdapter
from workflows.repair.control import (
    branch_summary,
    can_fanin,
    merge_same_invariant_candidates,
)
from agents.repair.execute_role import run_role
from model_config.agent_roles import AgentRole
from workflows.repair.schemas import (
    FanInPayload,
    HuntPayload,
    OwnerDecision,
    OwnerQuestion,
    RelationPayload,
    RepairState,
)

from agents.repair.helpers import READ_ONLY


def as_state(value: RepairState | Mapping[str, Any]) -> RepairState:
    """Normalize a raw mapping into the repair state model."""

    if isinstance(value, RepairState):
        return value
    return RepairState.model_validate(value)


def with_receipt(state: RepairState, receipt, **updates: Any) -> dict[str, Any]:
    """Return a LangGraph update that appends the worker receipt."""

    return {
        "runs": [*state.runs, receipt],
        "revision": state.revision + 1,
        **updates,
    }


class HuntNodes:
    """AI-backed hunt and fan-in performers for repair missions."""

    def __init__(self, adapter: ABCAdapter):
        self.adapter = adapter

    async def classify_relations(self, raw_state):
        state = as_state(raw_state)
        if not state.hunts:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "relation classification requires hunt result",
            }
        hunt = state.hunts[-1]
        if hunt.hunt_result == "CLEAR":
            return {
                "status": "RELATIONS_CLEAR",
                "current_stage": "relations",
            }
        if hunt.hunt_result == "INCONCLUSIVE":
            return {
                "status": "BLOCKED_INCONCLUSIVE",
                "current_stage": "finish",
                "error": "invariant hunt was inconclusive",
            }

        relations: list[RelationPayload] = []
        runs = list(state.runs)
        revision = state.revision
        for candidate in hunt.candidates:
            working = state.model_copy(
                update={"runs": runs, "revision": revision}
            )
            execution = await run_role(
                working,
                adapter=self.adapter,
                role=AgentRole.RELATION_CLASSIFIER,
                payload_model=RelationPayload,
                tools=READ_ONLY,
                payload={
                    "root_invariant": state.invariants[0].model_dump(mode="json"),
                    "candidate": candidate.model_dump(mode="json"),
                    "root_findings": [
                        outcome.model_dump(mode="json")
                        for outcome in state.finished
                    ],
                },
                instructions=(
                    "Classify this hunt candidate as SAME_INVARIANT_FINDING, "
                    "NEW_ROOT_CAUSE, FALSE_POSITIVE, OUT_OF_SCOPE or "
                    "INCONCLUSIVE. Do not repair or close it."
                ),
            )
            runs.append(execution.receipt)
            revision += 1
            if execution.payload is None:
                return {
                    "runs": runs,
                    "revision": revision,
                    "status": "BLOCKED",
                    "current_stage": "finish",
                    "error": execution.error,
                }
            relations.append(
                RelationPayload.model_validate(execution.payload)
            )

        pairs = list(zip(hunt.candidates, relations, strict=True))
        queue = merge_same_invariant_candidates(state, pairs)
        rejects = list(state.rejects)
        for candidate, relation in pairs:
            if relation.relation != "SAME_INVARIANT_FINDING":
                rejects.append(
                    {
                        "finding_id": candidate.hunt_candidate_id,
                        "relation": relation.relation,
                        "reason": relation.reason,
                        "linked_case_hint": relation.linked_case_hint,
                    }
                )

        return {
            "runs": runs,
            "revision": revision,
            "relations": [*state.relations, *relations],
            "queue": queue,
            "rejects": rejects,
            "status": "RELATIONS_CLASSIFIED",
            "current_stage": "relations",
            "error": None,
        }

    async def fanin(self, raw_state):
        state = as_state(raw_state)
        ready, blockers = can_fanin(state)
        if not ready:
            return {
                "status": "FANIN_INCOMPLETE",
                "current_stage": "fanin",
                "error": "blocking findings: " + ", ".join(blockers),
            }
        execution = await run_role(
            state,
            adapter=self.adapter,
            role=AgentRole.FAN_IN_ASSEMBLER,
            payload_model=FanInPayload,
            tools=READ_ONLY,
            payload={
                "root_invariant": state.invariants[0].model_dump(mode="json")
                if state.invariants
                else None,
                "branches": [
                    row.model_dump(mode="json")
                    for row in branch_summary(state.finished)
                ],
                "relations": [
                    relation.model_dump(mode="json")
                    for relation in state.relations
                ],
                "hunt_results": [
                    hunt.model_dump(mode="json")
                    for hunt in state.hunts
                ],
                "rejects": state.rejects,
            },
            instructions=(
                "Assemble the family-level fan-in only from completed evidence. "
                "Open SAME_INVARIANT or INCONCLUSIVE items must force "
                "FANIN_INCOMPLETE. Do not edit source/tests or invent Owner "
                "authority."
            ),
        )
        if execution.payload is None:
            return with_receipt(
                state,
                execution.receipt,
                status="BLOCKED",
                current_stage="finish",
                error=execution.error,
            )
        payload = FanInPayload.model_validate(execution.payload)
        return with_receipt(
            state,
            execution.receipt,
            fanin=payload,
            status=payload.status,
            current_stage="fanin",
            error=None
            if payload.status == "FANIN_READY"
            else "fanin incomplete",
        )

    def final_owner(self, raw_state):
        state = as_state(raw_state)
        if state.fanin is None or state.fanin.status != "FANIN_READY":
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "final owner decision requires FANIN_READY",
            }

        if state.owner_final:
            decision = state.owner_final
        elif state.owner_go is True:
            # Peer с owner_gate: nested verify (owner_go) без FINAL_CLASS_VERDICT interrupt.
            decision = "CLOSE_EPISODE_NO_CLASS_LESSON"
        else:
            question = OwnerQuestion(
                kind="FINAL_CLASS_VERDICT",
                mission_id=state.mission_id,
                question="Bug family is FANIN_READY. Choose final class verdict.",
                options=[
                    "PROMOTE_CLASS_LESSON",
                    "KEEP_LESSON_CANDIDATE_ONLY",
                    "CLOSE_EPISODE_NO_CLASS_LESSON",
                    "SPLIT_NEW_EPISODE",
                    "STOP",
                ],
            )
            raw = interrupt(question.model_dump(mode="json"))
            if isinstance(raw, str):
                decision = raw.strip().upper()
            elif isinstance(raw, Mapping):
                parsed = OwnerDecision.model_validate(raw)
                decision = parsed.decision
            else:
                decision = "STOP"

        terminal = {
            "PROMOTE_CLASS_LESSON": "COMPLETE_OWNER_PROMOTE",
            "KEEP_LESSON_CANDIDATE_ONLY": "COMPLETE_LESSON_CANDIDATE",
            "CLOSE_EPISODE_NO_CLASS_LESSON": "COMPLETE_NO_CLASS_LESSON",
            "SPLIT_NEW_EPISODE": "COMPLETE_SPLIT_REQUESTED",
        }.get(decision, "WAITING_OWNER")
        return {
            "owner_final": decision,
            "status": terminal,
            "current_stage": "finish",
            "error": None if terminal != "WAITING_OWNER" else "Owner stopped",
            "learning": {
                **state.learning,
                "owner_final": decision,
                "fanin_status": state.fanin.status,
            },
        }

