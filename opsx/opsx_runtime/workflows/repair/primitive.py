"""Run one Bug Team primitive against a durable RepairState JSON."""

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

from agents.common.commit_agent import CommitAgent
from model_config.agent_roles import AgentRole
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
from workflows.repair.control import claim_next, materialize_review
from agents.repair.green_verifier import GreenVerifierAgent
from agents.repair.fix_bug import FixBugAgent
from agents.repair.invariant_capture import InvariantCaptureAgent
from agents.repair.bounded_hunter import BoundedHunterAgent
from agents.repair.nodes_hunt import HuntNodes
from agents.repair.nodes_repair import RepairNodes
from workflows.repair.preflight import run_preflight
from workflows.repair.schemas import RepairState

DETERMINISTIC = {"MATERIALIZER", "CLAIM_WORKER", "PREFLIGHT"}
REPAIR_METHODS = {
    "CLASSIFIER": "classify",
    "RED_TEST_WORKER": "red",
    "OWNER_GATE": "owner_gate",
}
REPAIR_STANDALONE_AGENTS = {
    "GREEN_VERIFIER": GreenVerifierAgent,
    "IMPLEMENTATION_EXECUTOR": FixBugAgent,
    "INVARIANT_CAPTURE_WORKER": InvariantCaptureAgent,
    "BOUNDED_HUNTER": BoundedHunterAgent,
}
STANDALONE_HOP = {
    "IMPLEMENTATION_EXECUTOR": (
        fix_build_payload,
        fix_get_prompt,
        AgentRole.IMPLEMENTATION_EXECUTOR,
    ),
    "GREEN_VERIFIER": (
        green_build_payload,
        green_get_prompt,
        AgentRole.GREEN_VERIFIER,
    ),
    "INVARIANT_CAPTURE_WORKER": (
        invariant_build_payload,
        invariant_get_prompt,
        AgentRole.INVARIANT_CAPTURE_WORKER,
    ),
    "BOUNDED_HUNTER": (
        hunt_build_payload,
        hunt_get_prompt,
        AgentRole.BOUNDED_HUNTER,
    ),
}
HUNT_METHODS = {
    "RELATION_CLASSIFIER": "classify_relations",
    "FAN_IN_ASSEMBLER": "fanin",
    "FINAL_OWNER": "final_owner",
}
ALL_ROLES = tuple(
    DETERMINISTIC
    | set(REPAIR_METHODS)
    | set(REPAIR_STANDALONE_AGENTS)
    | set(HUNT_METHODS)
    | {"COMMIT"}
)


def _read(path: Path) -> RepairState:
    return RepairState.model_validate_json(path.read_text(encoding="utf-8"))


def _write(path: Path, state: RepairState) -> None:
    path.write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _merge(state: RepairState, update: dict[str, Any]) -> RepairState:
    raw = state.model_dump(mode="python")
    raw.update(update)
    return RepairState.model_validate(raw)


def _deterministic(role: str, state: RepairState) -> dict[str, Any]:
    if role == "PREFLIGHT":
        raise RuntimeError("PREFLIGHT is async; dispatch it from _run")
    if role == "MATERIALIZER":
        if state.source_review is None:
            raise ValueError("MATERIALIZER requires state.source_review")
        queue = materialize_review(state.source_review)
        return {
            "queue": queue,
            "status": "MATERIALIZED",
            "current_stage": "materialize",
            "error": None,
        }
    if role == "CLAIM_WORKER":
        active, queue = claim_next(state)
        return {
            "active": active,
            "queue": queue,
            "status": "CLAIMED" if active else "QUEUE_EMPTY",
            "current_stage": "claim",
            "error": None,
        }
    raise ValueError(role)


async def _run(args: argparse.Namespace) -> int:
    path = Path(args.state).resolve()
    state = _read(path)
    role = args.role

    if not args.live and role not in DETERMINISTIC and role != "COMMIT":
        print(json.dumps({
            "status": "DRY_RUN",
            "role": role,
            "mission_id": state.mission_id,
            "current_stage": state.current_stage,
            "workspace": state.workspace,
            "model": state.model.model_dump(mode="json"),
            "max_agent_input_tokens": state.max_agent_input_tokens,
        }, ensure_ascii=False, indent=2))
        return 0

    if role == "PREFLIGHT":
        update = await run_preflight(state)
    elif role == "COMMIT":
        from runtime_bootstrap import ensure_adapter_factories_registered

        ensure_adapter_factories_registered()
        from workflows.repair.repair_adapter_port import RepairAdapter

        commit_adapter = (
            RepairAdapter(timeout_seconds=args.timeout) if args.live else None
        )
        update = await CommitAgent(adapter=commit_adapter).run(
            state.model_dump(mode="json"),
        )
    elif role in DETERMINISTIC:
        update = _deterministic(role, state)
    else:
        if state.preflight is None or state.preflight.status != "READY":
            raise RuntimeError(
                "live agent primitive requires PREFLIGHT_READY; run --role PREFLIGHT first"
            )
        from runtime_bootstrap import ensure_adapter_factories_registered

        ensure_adapter_factories_registered()
        from workflows.repair.repair_adapter_port import RepairAdapter
        adapter = RepairAdapter(timeout_seconds=args.timeout)
        if role in REPAIR_STANDALONE_AGENTS:
            build_payload, get_prompt_fn, agent_role = STANDALONE_HOP[role]
            agent = REPAIR_STANDALONE_AGENTS[role](adapter)
            update = await agent.run(
                state.model_dump(mode="json"),
                prompt=get_prompt_fn(state, role=agent_role),
                payload=build_payload(state),
            )
        elif role in REPAIR_METHODS:
            nodes = RepairNodes(adapter)
            method = getattr(nodes, REPAIR_METHODS[role])
            value = method(state.model_dump(mode="json"))
            update = await value if hasattr(value, "__await__") else value
        else:
            nodes = HuntNodes(adapter)
            method = getattr(nodes, HUNT_METHODS[role])
            value = method(state.model_dump(mode="json"))
            update = await value if hasattr(value, "__await__") else value

    updated = _merge(state, update)
    target = Path(args.output).resolve() if args.output else path
    _write(target, updated)
    from workflows.repair.durable_export import persist_durable_exports

    persist_durable_exports(updated)
    print(json.dumps({
        "status": updated.status,
        "role": role,
        "mission_id": updated.mission_id,
        "current_stage": updated.current_stage,
        "error": updated.error,
        "state_path": str(target),
    }, ensure_ascii=False, indent=2))
    return 0 if updated.error is None else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run one LangGraph Bug Team primitive")
    p.add_argument("--state", required=True)
    p.add_argument("--role", choices=sorted(ALL_ROLES), required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument("--output")
    p.add_argument("--timeout", type=float, default=7200)
    return p


def main() -> None:
    raise SystemExit(asyncio.run(_run(parser().parse_args())))


if __name__ == "__main__":
    main()
