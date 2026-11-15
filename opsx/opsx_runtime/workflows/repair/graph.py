"""Composable LangGraph graphs for the restored Bug Team.

Layers:
- repair subgraph: one finding through classify/RED/FIX/GREEN/invariant;
- hunt subgraph: invariant hunt + relation classification;
- bug-team graph: queue/materialize/claim + nested repair + repeated hunt + fan-in.

The package is deliberately not wired into workflows/main_graph.py.
"""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.graph import END, START, StateGraph

from agents.adapters.abc_adapter import ABCAdapter
from agents.common.commit_agent import CommitAgent
from agents.repair.fix_bug.prompt.get_prompt import (
    build_request_payload as fix_build_payload,
    get_prompt as fix_get_prompt,
)
from agents.repair.green_verifier.prompt.get_prompt import (
    build_request_payload as green_build_payload,
    get_prompt as green_get_prompt,
)
from agents.repair.invariant_capture.prompt.get_prompt import (
    build_request_payload as invariant_build_payload,
    get_prompt as invariant_get_prompt,
)
from agents.repair.bounded_hunter.prompt.get_prompt import (
    build_request_payload as hunt_build_payload,
    get_prompt as hunt_get_prompt,
)
from agents.repair.green_verifier import GreenVerifierAgent
from agents.repair.fix_bug import FixBugAgent
from agents.repair.invariant_capture import InvariantCaptureAgent
from agents.repair.bounded_hunter import BoundedHunterAgent
from model_config.agent_roles import AgentRole

from .control import (
    can_fanin,
    can_post_complete_rehunt,
    claim_next,
    classification_route,
    family_convergence,
    green_route,
    materialize_review,
    post_complete_rehunt_update,
    red_route,
)
from agents.repair.nodes_hunt import HuntNodes
from agents.repair.nodes_repair import RepairNodes
from .preflight import run_preflight
from .progress import emit_repair_progress
from .schemas import RepairState


def as_state(value: RepairState | Mapping[str, Any]) -> RepairState:
    if isinstance(value, RepairState):
        return value
    return RepairState.model_validate(value)


def build_repair_graph(
    adapter: ABCAdapter,
    *,
    checkpointer=None,
    start_at: str = "classify",
    include_preflight: bool = True,
):
    """One finding lifecycle, optionally restarted from a durable stage."""
    allowed_starts = {
        "classify",
        "red",
        "owner_gate",
        "fix",
        "green",
        "invariant",
    }
    if start_at not in allowed_starts:
        raise ValueError(
            f"unsupported repair start stage {start_at!r}; "
            f"expected one of {sorted(allowed_starts)}"
        )
    nodes = RepairNodes(adapter)
    commit_agent = CommitAgent(adapter=adapter)
    fix_agent = FixBugAgent(adapter)
    green_agent = GreenVerifierAgent(adapter)
    invariant_agent = InvariantCaptureAgent(adapter)

    async def fix(raw: Mapping[str, Any]) -> dict[str, Any]:
        state = as_state(raw)
        return await fix_agent.run(
            raw,
            prompt=fix_get_prompt(state, role=AgentRole.IMPLEMENTATION_EXECUTOR),
            payload=fix_build_payload(state),
        )

    async def green(raw: Mapping[str, Any]) -> dict[str, Any]:
        state = as_state(raw)
        return await green_agent.run(
            raw,
            prompt=green_get_prompt(state, role=AgentRole.GREEN_VERIFIER),
            payload=green_build_payload(state),
        )

    async def invariant(raw: Mapping[str, Any]) -> dict[str, Any]:
        state = as_state(raw)
        return await invariant_agent.run(
            raw,
            prompt=invariant_get_prompt(state, role=AgentRole.INVARIANT_CAPTURE_WORKER),
            payload=invariant_build_payload(state),
        )

    async def commit(raw: Mapping[str, Any]) -> dict[str, Any]:
        emit_repair_progress(
            event="stage",
            stage="COMMIT",
            state="running",
            detail="Сохраняем проверенное исправление",
        )
        result = await commit_agent.run(raw)
        commit_status = str(result.get("commit_status") or "UNKNOWN")
        failed = commit_status in {"BLOCKED", "FAILED"}
        emit_repair_progress(
            event="stage",
            stage="COMMIT",
            state="failed" if failed else "completed",
            detail=commit_status,
            data={
                "status": commit_status,
                "commit_sha": result.get("commit_sha"),
                "files_changed": result.get("commit_files") or [],
            },
        )
        return result

    graph = StateGraph(RepairState)

    graph.add_node("classify", nodes.classify)
    graph.add_node("red", nodes.red)
    graph.add_node("owner_gate", nodes.owner_gate)
    graph.add_node("fix", fix)
    graph.add_node("commit", commit)
    graph.add_node("green", green)
    graph.add_node("invariant", invariant)
    graph.add_node("complete", nodes.complete)
    graph.add_node("terminalize", nodes.terminalize_nonrepair)
    graph.add_node("opsx_new", nodes.route_opsx_new)
    graph.add_node("archived_delta", nodes.route_archived_delta)
    graph.add_node("opsx_update", nodes.route_opsx_update)
    graph.add_node("out_of_scope", nodes.route_out_of_scope)
    graph.add_node("not_reproducing", nodes.not_reproducing)
    if include_preflight:
        graph.add_node("preflight", run_preflight)
        graph.add_edge(START, "preflight")
        graph.add_conditional_edges(
            "preflight",
            lambda raw: start_at if as_state(raw).status == "PREFLIGHT_READY" else END,
            {start_at: start_at, END: END},
        )
    else:
        graph.add_edge(START, start_at)

    def after_classify(raw):
        state = as_state(raw)
        if state.classification is None:
            return END
        route = classification_route(state.classification)
        if route == "repair":
            return "red"
        if route in {"reject", "blocked"}:
            return "terminalize"
        return route

    graph.add_conditional_edges(
        "classify",
        after_classify,
        {
            "red": "red",
            "terminalize": "terminalize",
            "opsx_new": "opsx_new",
            "archived_delta": "archived_delta",
            "opsx_update": "opsx_update",
            "out_of_scope": "out_of_scope",
            END: END,
        },
    )

    def after_red(raw):
        state = as_state(raw)
        if state.red is None:
            return END
        route = red_route(state.red.status)
        if route == "owner":
            return "owner_gate"
        if route == "not_reproducing":
            return "not_reproducing"
        return END

    graph.add_conditional_edges(
        "red",
        after_red,
        {
            "owner_gate": "owner_gate",
            "not_reproducing": "not_reproducing",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "owner_gate",
        lambda raw: "fix" if as_state(raw).owner_go is True else END,
        {"fix": "fix", END: END},
    )
    graph.add_conditional_edges(
        "fix",
        lambda raw: (
            "commit"
            if as_state(raw).fix is not None
            and as_state(raw).fix.status == "FIXED"
            else END
        ),
        {"commit": "commit", END: END},
    )
    graph.add_conditional_edges(
        "commit",
        lambda raw: (
            "green"
            if as_state(raw).fix is not None
            and as_state(raw).fix.status == "FIXED"
            and as_state(raw).commit_status
            not in {"BLOCKED", "FAILED"}
            else END
        ),
        {"green": "green", END: END},
    )
    def after_green(raw: Mapping[str, Any]) -> str:
        state = as_state(raw)
        if (
            state.green is not None
            and green_route(state.green.status) == "invariant"
            and not state.error
        ):
            return "invariant"
        if state.active is not None:
            return "complete"
        return END

    graph.add_conditional_edges(
        "green",
        after_green,
        {"invariant": "invariant", "complete": "complete", END: END},
    )
    graph.add_edge("invariant", "complete")
    graph.add_edge("complete", END)
    graph.add_edge("terminalize", END)
    graph.add_edge("not_reproducing", END)
    return graph.compile(checkpointer=checkpointer)


def build_hunt_graph(
    adapter: ABCAdapter,
    *,
    checkpointer=None,
    include_preflight: bool = True,
):
    """Standalone invariant hunt that can be invoked independently."""
    hunt_agent = BoundedHunterAgent(adapter)
    nodes = HuntNodes(adapter)

    async def hunt(raw: Mapping[str, Any]) -> dict[str, Any]:
        state = as_state(raw)
        return await hunt_agent.run(
            raw,
            prompt=hunt_get_prompt(state, role=AgentRole.BOUNDED_HUNTER),
            payload=hunt_build_payload(state),
        )

    graph = StateGraph(RepairState)
    if include_preflight:
        graph.add_node("preflight", run_preflight)
    graph.add_node("hunt", hunt)
    graph.add_node("relations", nodes.classify_relations)
    if include_preflight:
        graph.add_edge(START, "preflight")
        graph.add_conditional_edges(
            "preflight",
            lambda raw: "hunt" if as_state(raw).status == "PREFLIGHT_READY" else END,
            {"hunt": "hunt", END: END},
        )
    else:
        graph.add_edge(START, "hunt")
    graph.add_conditional_edges(
        "hunt",
        lambda raw: (
            "relations"
            if as_state(raw).hunts
            and as_state(raw).hunts[-1].hunt_result == "CANDIDATES_FOUND"
            else END
        ),
        {"relations": "relations", END: END},
    )
    graph.add_edge("relations", END)
    return graph.compile(checkpointer=checkpointer)


def build_bug_team_graph(
    adapter: ABCAdapter,
    *,
    checkpointer=None,
    include_preflight: bool = True,
):
    """Full composable Bug Team loop.

    Logical fan-out is materialized into the durable queue. Repair writes are
    deliberately serialized in one isolated workspace; this follows the
    existing write-set rule forbidding uncoordinated concurrent production
    writes. Classification/hunt remain independent agent hops.
    """
    repair = build_repair_graph(adapter, include_preflight=False)
    hunt_nodes = HuntNodes(adapter)
    hunt_agent = BoundedHunterAgent(adapter)
    graph = StateGraph(RepairState)

    async def hunt(raw: Mapping[str, Any]) -> dict[str, Any]:
        state = as_state(raw)
        return await hunt_agent.run(
            raw,
            prompt=hunt_get_prompt(state, role=AgentRole.BOUNDED_HUNTER),
            payload=hunt_build_payload(state),
        )

    def materialize(raw):
        state = as_state(raw)
        queue = list(state.queue)
        if not queue and state.source_review is not None:
            queue = materialize_review(state.source_review)
        if len(queue) > state.max_items:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": (
                    f"materialized items {len(queue)} exceed "
                    f"max_items={state.max_items}"
                ),
            }
        return {
            "queue": queue,
            "status": "MATERIALIZED",
            "current_stage": "materialize",
            "error": None,
        }

    def claim(raw):
        state = as_state(raw)
        finding, remaining = claim_next(state)
        if finding is None:
            return {
                "active": None,
                "queue": remaining,
                "status": "QUEUE_EMPTY",
                "current_stage": "claim",
            }
        return {
            "active": finding,
            "queue": remaining,
            "status": "CLAIMED",
            "current_stage": "claim",
            "error": None,
        }

    def family_gate(raw):
        state = as_state(raw)
        ready, blockers = family_convergence(state)
        return {
            "status": "FAMILY_CONVERGED" if ready else "BLOCKED",
            "current_stage": "family_gate" if ready else "finish",
            "error": None if ready else "family blockers: " + ", ".join(blockers),
        }

    def no_family_finish(raw):
        state = as_state(raw)
        terminal = (
            "COMPLETE_NO_REPAIR"
            if state.finished
            else "COMPLETE_NO_FINDINGS"
        )
        return {
            "status": terminal,
            "current_stage": "finish",
            "error": None,
        }

    if include_preflight:
        graph.add_node("preflight", run_preflight)
    graph.add_node("materialize", materialize)
    graph.add_node("claim", claim)
    graph.add_node("repair", repair)
    graph.add_node("hunt", hunt)
    graph.add_node("relations", hunt_nodes.classify_relations)
    graph.add_node("family_gate", family_gate)
    graph.add_node("fanin", hunt_nodes.fanin)
    graph.add_node("final_owner", hunt_nodes.final_owner)
    graph.add_node("finish_no_family", no_family_finish)

    def post_complete_rehunt(raw):
        state = as_state(raw)
        return post_complete_rehunt_update(state)

    graph.add_node("post_complete_rehunt", post_complete_rehunt)

    if include_preflight:
        graph.add_edge(START, "preflight")
        graph.add_conditional_edges(
            "preflight",
            lambda raw: "materialize" if as_state(raw).status == "PREFLIGHT_READY" else END,
            {"materialize": "materialize", END: END},
        )
    else:
        graph.add_edge(START, "materialize")
    graph.add_conditional_edges(
        "materialize",
        lambda raw: (
            END
            if as_state(raw).error
            else "claim"
            if as_state(raw).queue
            else "finish_no_family"
        ),
        {
            "claim": "claim",
            "finish_no_family": "finish_no_family",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "claim",
        lambda raw: "repair" if as_state(raw).active is not None else (
            "family_gate" if as_state(raw).invariants else "finish_no_family"
        ),
        {
            "repair": "repair",
            "family_gate": "family_gate",
            "finish_no_family": "finish_no_family",
        },
    )

    def after_repair(raw):
        state = as_state(raw)
        if state.error or state.status in {
            "BLOCKED",
            "WAITING_OWNER",
            "BLOCKED_INCONCLUSIVE",
        }:
            return END
        if state.invariants and state.finished:
            latest = state.finished[-1]
            if latest.terminal_state == "FIXED":
                return "hunt"
        if state.queue:
            return "claim"
        if state.invariants:
            return "hunt"
        return "finish_no_family"

    graph.add_conditional_edges(
        "repair",
        after_repair,
        {
            "hunt": "hunt",
            "claim": "claim",
            "finish_no_family": "finish_no_family",
            END: END,
        },
    )

    def after_hunt(raw):
        state = as_state(raw)
        if state.error:
            return END
        if not state.hunts:
            return END
        result = state.hunts[-1].hunt_result
        if result == "CANDIDATES_FOUND":
            return "relations"
        if result == "CLEAR":
            return "claim" if state.queue else "family_gate"
        return END

    graph.add_conditional_edges(
        "hunt",
        after_hunt,
        {
            "relations": "relations",
            "claim": "claim",
            "family_gate": "family_gate",
            END: END,
        },
    )

    def after_relations(raw):
        state = as_state(raw)
        if state.error or state.status == "BLOCKED_INCONCLUSIVE":
            return END
        if state.queue:
            return "claim"
        # A non-blocking classification does not prove ecosystem closure.
        # Re-hunt the root invariant until a later pass reports CLEAR.
        return "hunt"

    graph.add_conditional_edges(
        "relations",
        after_relations,
        {"claim": "claim", "hunt": "hunt", END: END},
    )
    graph.add_conditional_edges(
        "family_gate",
        lambda raw: "fanin" if not as_state(raw).error else END,
        {"fanin": "fanin", END: END},
    )
    graph.add_conditional_edges(
        "fanin",
        lambda raw: (
            "final_owner"
            if as_state(raw).fanin is not None
            and as_state(raw).fanin.status == "FANIN_READY"
            else END
        ),
        {"final_owner": "final_owner", END: END},
    )
    graph.add_conditional_edges(
        "final_owner",
        lambda raw: (
            "post_complete_rehunt"
            if can_post_complete_rehunt(as_state(raw))
            else END
        ),
        {"post_complete_rehunt": "post_complete_rehunt", END: END},
    )
    graph.add_edge("post_complete_rehunt", "hunt")
    graph.add_edge("finish_no_family", END)

    return graph.compile(checkpointer=checkpointer)
