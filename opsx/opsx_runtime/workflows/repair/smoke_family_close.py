"""Smoke: закрытие Bug Team семьи после BOUNDED_HUNTER CLEAR.

Проверяет prerequisites, применяет family_gate, live FAN_IN_ASSEMBLER и
FINAL_OWNER (без LangGraph interrupt при --owner-final).

Typical use after manual primitives on a mission state:

    python -B opsx/opsx_runtime/workflows/repair/smoke_family_close.py \\
        --state opsx/runs/repair/<mission>/latest_state.json \\
        --live --owner-final CLOSE_EPISODE_NO_CLASS_LESSON
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

from agents.repair.nodes_hunt import HuntNodes
from workflows.repair.control import family_convergence
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


def _validate_chain(state: RepairState) -> list[str]:
    """Проверки цепочки hunt → family close."""
    errors: list[str] = []
    if not state.invariants:
        errors.append("missing invariants[]")
    if not state.finished:
        errors.append("missing finished[] (run repair complete first)")
    if not state.hunts:
        errors.append("missing hunts[] (run BOUNDED_HUNTER first)")
    elif state.hunts[-1].hunt_result != "CLEAR":
        errors.append(
            f"last hunt_result={state.hunts[-1].hunt_result!r}, expected CLEAR"
        )
    if state.active is not None:
        errors.append(f"active finding still set: {state.active.id}")
    if state.queue:
        errors.append(f"queue not empty: {[f.id for f in state.queue]}")
    for outcome in state.finished:
        if outcome.terminal_state != "FIXED":
            errors.append(f"{outcome.finding.id} terminal_state={outcome.terminal_state}")
    return errors


def _family_gate_update(state: RepairState) -> dict[str, Any]:
    ready, blockers = family_convergence(state)
    return {
        "status": "FAMILY_CONVERGED" if ready else "BLOCKED",
        "current_stage": "family_gate" if ready else "finish",
        "error": None if ready else "family blockers: " + ", ".join(blockers),
    }


def _artifact_report(state: RepairState) -> dict[str, Any]:
    workers = Path(state.output_dir) / "workers"
    if not workers.is_dir():
        return {"workers_dir": str(workers), "files": []}
    names = sorted(p.name for p in workers.iterdir() if p.is_file())
    roles = {
        "INVARIANT_CAPTURE_WORKER": "invariant-capture",
        "BOUNDED_HUNTER": "bounded-hunter",
        "FAN_IN_ASSEMBLER": "fan-in-assembler",
    }
    found: dict[str, list[str]] = {}
    for role, slug in roles.items():
        found[role] = [n for n in names if slug in n and n.endswith(".json")]
    return {"workers_dir": str(workers), "artifact_receipts_by_role": found}


async def _run(args: argparse.Namespace) -> int:
    path = Path(args.state).resolve()
    state = _load(path)
    chain_errors = _validate_chain(state)
    report = {
        "mission_id": state.mission_id,
        "chain_validation": chain_errors or ["OK"],
        "artifacts_before": _artifact_report(state),
    }
    if chain_errors:
        print(json.dumps({**report, "status": "CHAIN_INVALID"}, indent=2))
        return 2

    gate = _family_gate_update(state)
    state = _merge(state, gate)
    report["family_gate"] = gate
    if gate["status"] != "FAMILY_CONVERGED":
        _save(path, state)
        print(json.dumps({**report, "status": "FAMILY_GATE_BLOCKED"}, indent=2))
        return 2

    if not args.live:
        print(
            json.dumps(
                {
                    **report,
                    "status": "DRY_RUN",
                    "next": "FAN_IN_ASSEMBLER + FINAL_OWNER with --live",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if state.preflight is None or state.preflight.status != "READY":
        print(
            json.dumps(
                {**report, "status": "BLOCKED", "error": "preflight not READY"},
                indent=2,
            )
        )
        return 2

    from workflows.repair.repair_adapter_port import RepairAdapter

    adapter = RepairAdapter(timeout_seconds=args.timeout)
    nodes = HuntNodes(adapter)

    fanin_update = await nodes.fanin(state.model_dump(mode="json"))
    state = _merge(state, fanin_update)
    report["fanin"] = {
        "status": state.status,
        "fanin_status": state.fanin.status if state.fanin else None,
        "error": state.error,
    }
    if state.fanin is None or state.fanin.status != "FANIN_READY":
        _save(path, state)
        print(json.dumps({**report, "status": "FANIN_FAILED"}, indent=2))
        return 2

    state = state.model_copy(update={"owner_final": args.owner_final})
    final_update = nodes.final_owner(state.model_dump(mode="json"))
    state = _merge(state, final_update)
    _save(path, state)

    report["final_owner"] = {
        "owner_final": state.owner_final,
        "status": state.status,
        "current_stage": state.current_stage,
        "error": state.error,
    }
    report["artifacts_after"] = _artifact_report(state)
    print(json.dumps({**report, "status": "SMOKE_FAMILY_CLOSE_OK"}, indent=2))
    return 0 if state.error is None else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke close hunt family after CLEAR")
    p.add_argument("--state", required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument(
        "--owner-final",
        default="CLOSE_EPISODE_NO_CLASS_LESSON",
        help="Skip LangGraph interrupt for FINAL_OWNER",
    )
    p.add_argument("--timeout", type=float, default=7200)
    return p


def main() -> None:
    raise SystemExit(asyncio.run(_run(parser().parse_args())))


if __name__ == "__main__":
    main()
