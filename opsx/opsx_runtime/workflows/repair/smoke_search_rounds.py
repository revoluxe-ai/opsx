"""Post-complete verification: после CLOSE с FIXED — повторный bounded hunt (max_search_rounds).

Typical после ``smoke_family_close`` / ``COMPLETE_*``:

    python -B opsx/opsx_runtime/workflows/repair/smoke_search_rounds.py \\
        --state opsx/runs/repair/<mission>/latest_state.json \\
        --live --max-search-rounds 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from agents.repair.bounded_hunter import BoundedHunterAgent
from agents.repair.bounded_hunter.prompt.get_prompt import (
    build_request_payload as hunt_build_payload,
    get_prompt as hunt_get_prompt,
)
from agents.repair.nodes_hunt import HuntNodes
from model_config.agent_roles import AgentRole
from workflows.repair.control import (
    can_post_complete_rehunt,
    family_convergence,
    post_complete_rehunt_update,
)
from workflows.repair.durable_export import persist_durable_exports
from workflows.repair.schemas import RepairState


def _load(path: Path) -> RepairState:
    return RepairState.model_validate_json(path.read_text(encoding="utf-8"))


def _save(path: Path, state: RepairState) -> None:
    path.write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    persist_durable_exports(state)


def _merge(state: RepairState, update: dict[str, Any]) -> RepairState:
    raw = state.model_dump(mode="python")
    raw.update(update)
    return RepairState.model_validate(raw)


def _family_gate_update(state: RepairState) -> dict[str, Any]:
    ready, blockers = family_convergence(state)
    return {
        "status": "FAMILY_CONVERGED" if ready else "BLOCKED",
        "current_stage": "family_gate" if ready else "finish",
        "error": None if ready else "family blockers: " + ", ".join(blockers),
    }


async def _close_family(
    state: RepairState,
    *,
    adapter: Any,
    owner_final: str,
) -> tuple[RepairState, dict[str, Any]]:
    """family_gate → fanin → final_owner."""
    nodes = HuntNodes(adapter)
    report: dict[str, Any] = {}

    gate = _family_gate_update(state)
    state = _merge(state, gate)
    report["family_gate"] = gate
    if gate["status"] != "FAMILY_CONVERGED":
        return state, report

    fanin_update = await nodes.fanin(state.model_dump(mode="json"))
    state = _merge(state, fanin_update)
    report["fanin"] = {
        "status": state.status,
        "fanin_status": state.fanin.status if state.fanin else None,
        "error": state.error,
    }
    if state.fanin is None or state.fanin.status != "FANIN_READY":
        return state, report

    state = state.model_copy(update={"owner_final": owner_final})
    final_update = nodes.final_owner(state.model_dump(mode="json"))
    state = _merge(state, final_update)
    report["final_owner"] = {
        "owner_final": state.owner_final,
        "status": state.status,
        "error": state.error,
    }
    return state, report


async def _run(args: argparse.Namespace) -> int:
    path = Path(args.state).resolve()
    state = _load(path)
    state = state.model_copy(
        update={"max_search_rounds": args.max_search_rounds}
    )

    report: dict[str, Any] = {
        "mission_id": state.mission_id,
        "max_search_rounds": state.max_search_rounds,
        "search_round_start": state.search_round,
        "cycles": [],
    }

    if not can_post_complete_rehunt(state) and not args.force:
        report["status"] = "SKIP_NO_REHUNT"
        report["reason"] = (
            "can_post_complete_rehunt=false "
            f"(status={state.status!r}, search_round={state.search_round})"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.live:
        report["status"] = "DRY_RUN"
        report["would_run"] = min(
            state.max_search_rounds - state.search_round,
            state.max_search_rounds,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if state.preflight is None or state.preflight.status != "READY":
        report["status"] = "BLOCKED"
        report["error"] = "preflight not READY"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    from workflows.repair.repair_adapter_port import RepairAdapter

    adapter = RepairAdapter(timeout_seconds=args.timeout)
    hunter = BoundedHunterAgent(adapter)
    nodes = HuntNodes(adapter)

    while can_post_complete_rehunt(state):
        cycle: dict[str, Any] = {"search_round_before": state.search_round}
        state = _merge(state, post_complete_rehunt_update(state))
        cycle["search_round_after_rehunt_arm"] = state.search_round

        hunt_update = await hunter.run(
            state.model_dump(mode="json"),
            prompt=hunt_get_prompt(state, role=AgentRole.BOUNDED_HUNTER),
            payload=hunt_build_payload(state),
        )
        state = _merge(state, hunt_update)
        cycle["hunt"] = {
            "status": state.status,
            "hunt_result": state.hunts[-1].hunt_result if state.hunts else None,
            "error": state.error,
        }
        if state.error:
            report["cycles"].append(cycle)
            report["status"] = "HUNT_FAILED"
            _save(path, state)
            print(json.dumps({**report, "error": state.error}, indent=2))
            return 2

        if not state.hunts:
            cycle["stop"] = "no_hunts"
            report["cycles"].append(cycle)
            break

        result = state.hunts[-1].hunt_result
        if result == "CANDIDATES_FOUND":
            rel_update = await nodes.classify_relations(state.model_dump(mode="json"))
            state = _merge(state, rel_update)
            cycle["relations"] = {
                "status": state.status,
                "queue_len": len(state.queue),
                "error": state.error,
            }
            report["cycles"].append(cycle)
            report["status"] = "CANDIDATES_NEED_REPAIR_GRAPH"
            _save(path, state)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

        if result != "CLEAR":
            cycle["stop"] = f"unexpected_hunt_result={result}"
            report["cycles"].append(cycle)
            break

        state, close_report = await _close_family(
            state,
            adapter=adapter,
            owner_final=args.owner_final,
        )
        cycle.update(close_report)
        report["cycles"].append(cycle)
        _save(path, state)

        if state.error or not str(state.status).startswith("COMPLETE_"):
            report["status"] = "CLOSE_FAILED"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    report["search_round_end"] = state.search_round
    report["final_status"] = state.status
    report["status"] = "SMOKE_SEARCH_ROUNDS_OK"
    _save(path, state)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Post-complete bounded-hunt verification rounds"
    )
    p.add_argument("--state", required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument("--force", action="store_true", help="deprecated; same as default when eligible")
    p.add_argument("--max-search-rounds", type=int, default=3)
    p.add_argument(
        "--owner-final",
        default="CLOSE_EPISODE_NO_CLASS_LESSON",
    )
    p.add_argument("--timeout", type=float, default=7200)
    return p


def main() -> None:
    raise SystemExit(asyncio.run(_run(parser().parse_args())))


if __name__ == "__main__":
    main()
