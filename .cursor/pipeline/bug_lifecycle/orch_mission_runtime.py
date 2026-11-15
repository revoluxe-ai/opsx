"""S0–S11 native orch-bug-fix mission driver (STROY authority)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import batch_clear, bug_team_authority, evolution_handoff, family_plan, intake_clear, materialize_bug_queue
from .disposition_route import (
    ROUTE_GLOBAL_BLOCKER,
    ROUTE_MIXED_SPLIT,
    ROUTE_MUST_FIX_REPAIR,
    ROUTE_NON_REPAIR,
    ROUTE_SECURITY_PROCESS,
    ROUTE_TECH_DEBT,
    route_work_items,
)
from .worktree_ownership import reconcile_worktree_ownership, require_receipts_for_s4
from .materialize_bug_queue import MaterializeContext, MaterializeError
from .hermetic_boundary import hermetic_execution_enabled
from .mission_identity import bind_identity, verify_identity
from .s0_intake_checkpoint import persist_intake_checkpoint, reconcile_work_items_from_checkpoint
from .stage_authority import (
    Stage,
    auto_persist_stage_for_command,
    next_stage,
    record_stage_complete,
    ssca_allows_advance,
)

PROCESS_ROUTE = "/opsx-meta-fix-command-defects"


def production_mode(repo_root: Path) -> bool:
    return not hermetic_execution_enabled(repo_root)


def _state_path(repo_root: Path, mission_id: str) -> Path:
    return repo_root / "opsx" / "runs" / "orch-missions" / mission_id / "runtime_state.json"


def load_state(repo_root: Path, mission_id: str) -> dict[str, Any]:
    path = _state_path(repo_root, mission_id)
    if not path.is_file():
        return {"stage": Stage.S0.value, "work_items": [], "completed_stages": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"stage": Stage.S0.value, "work_items": [], "completed_stages": []}
    return value if isinstance(value, dict) else {"stage": Stage.S0.value, "work_items": [], "completed_stages": []}


def store_state(repo_root: Path, mission_id: str, state: dict[str, Any]) -> None:
    path = _state_path(repo_root, mission_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ctx(repo_root: Path, request: dict[str, Any]) -> MaterializeContext:
    return MaterializeContext(
        repo_root=repo_root,
        mission_id=str(request.get("mission") or ""),
        execution_id=str(request.get("execution_id") or ""),
        head=str(request.get("head") or ""),
        change_id=str(request.get("change") or ""),
        production=production_mode(repo_root),
    )


def _findings(request: dict[str, Any]) -> list[dict[str, str]]:
    raw = request.get("findings")
    if isinstance(raw, list):
        rows: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, dict):
                rows.append(
                    {
                        "location": str(item.get("location") or ""),
                        "finding": str(item.get("finding") or item.get("id") or ""),
                    }
                )
        return rows
    return []


def _advance_to(repo_root: Path, mission_id: str, state: dict[str, Any], stage: Stage) -> None:
    record_stage_complete(state, stage)
    nxt = next_stage(stage)
    state["stage"] = (nxt or Stage.DONE).value
    store_state(repo_root, mission_id, state)


def _s4_repair_route(route: str | None, item: dict[str, Any]) -> bool:
    if route in {ROUTE_MUST_FIX_REPAIR, ROUTE_MIXED_SPLIT}:
        return True
    return bool(item.get("s4_repair_required"))


def advance(repo_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(request.get("mission") or "")
    if not mission_id:
        return {"STATUS": "FAIL_CLOSED", "REASON": "missing_mission"}
    state = load_state(repo_root, mission_id)
    stage = Stage(state.get("stage", Stage.S0.value))
    ctx = _ctx(repo_root, request)
    findings = _findings(request)
    prod = production_mode(repo_root)
    identity_ok, identity_reason = verify_identity(state, request, findings)
    if not identity_ok:
        if stage == Stage.DONE and findings and identity_reason.startswith("identity_"):
            return {
                "STATUS": "FAIL_CLOSED",
                "REASON": "terminal_mission_superseded",
                "NEXT_ACTION": "mint_new_mission_for_current_findings",
                "MISSION_ID": mission_id,
            }
        return {"STATUS": "FAIL_CLOSED", "REASON": identity_reason, "MISSION_ID": mission_id}

    allowed, reason = ssca_allows_advance(state, stage)
    if not allowed:
        return {"STATUS": "FAIL_CLOSED", "REASON": reason, "MISSION_ID": mission_id, "STAGE": stage.value}

    if stage == Stage.S0:
        cleared = intake_clear.normalize_findings(findings)
        if cleared["STATUS"] != "CLEAR_COMPLETE":
            return {**cleared, "MISSION_ID": mission_id}
        if cleared["PROCESS_FINDINGS"] and not cleared["CLEAN_FINDINGS"]:
            return {
                "STATUS": "PARTIAL_COMPLETE",
                "BUG_BATCH_CLEAR": "NO",
                "PROCESS_HANDOFF": "YES",
                "NEXT_COMMAND": PROCESS_ROUTE,
                "MISSION_ID": mission_id,
            }
        clean_rows = cleared["CLEAN_FINDINGS"]
        work_items = reconcile_work_items_from_checkpoint(
            repo_root,
            mission_id=mission_id,
            request=request,
            findings=clean_rows,
        )
        if work_items is None:
            work_items = []
            for row in clean_rows:
                finding_id = row["finding"] or "F-UNKNOWN"
                intake = materialize_bug_queue.mode_intake(
                    ctx,
                    finding_id=finding_id,
                    location=row.get("location", ""),
                    raw_report=f"orch intake {finding_id}",
                )
                work_items.append(
                    {
                        "finding_id": finding_id,
                        "case_id": intake["CASE_ID"],
                        "location": row.get("location", ""),
                    }
                )
            persist_intake_checkpoint(
                repo_root,
                mission_id=mission_id,
                request=request,
                findings=clean_rows,
                work_items=work_items,
            )
        state["work_items"] = work_items
        state["process_parallel"] = cleared["PROCESS_FINDINGS"]
        bind_identity(state, request, findings)
        _advance_to(repo_root, mission_id, state, Stage.S0)
        out: dict[str, Any] = {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S0.value,
            "NEXT_STAGE": Stage.S1B.value,
            "WORK_ITEMS": work_items,
            "MISSION_ID": mission_id,
        }
        if cleared["PROCESS_FINDINGS"]:
            out["PROCESS_HANDOFF"] = "YES"
            out["MIXED_BATCH_POLICY"] = "PRODUCT_PP_CONTINUES"
            out["PROCESS_FINDINGS"] = cleared["PROCESS_FINDINGS"]
        return out

    if stage == Stage.S1B:
        plan = family_plan.persist_family_plan(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
            change_id=ctx.change_id,
            clean_findings=[
                {"finding": w["finding_id"], "location": w.get("location", "")}
                for w in state.get("work_items", [])
            ],
        )
        for item, wi in zip(state.get("work_items", []), plan["WORK_ITEMS"], strict=False):
            item["work_item_id"] = wi["work_item_id"]
            item["plan_id"] = plan["PLAN_ID"]
        _advance_to(repo_root, mission_id, state, Stage.S1B)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S1B.value,
            "NEXT_STAGE": Stage.S2B.value,
            "PLAN_ID": plan["PLAN_ID"],
            "RECEIPT": plan["RECEIPT"],
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S2B:
        receipts = []
        for item in state.get("work_items", []):
            receipts.append(
                materialize_bug_queue.mode_update_disposition(
                    ctx,
                    case_id=item["case_id"],
                    finding_id=item["finding_id"],
                    plan_id=item.get("plan_id", ""),
                    work_item_id=item.get("work_item_id", ""),
                )
            )
        _advance_to(repo_root, mission_id, state, Stage.S2B)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S2B.value,
            "NEXT_STAGE": Stage.S3.value,
            "DISPOSITION_RECEIPTS": receipts,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S3:
        routed, blocked = route_work_items(repo_root, state.get("work_items", []))
        if blocked:
            return {
                "STATUS": "FAIL_CLOSED",
                "REASON": "unresolved_disposition",
                "UNRESOLVED": blocked,
                "MISSION_ID": mission_id,
            }
        state["routed_work_items"] = routed
        for item in state.get("work_items", []):
            for row in routed:
                if row["finding_id"] == item["finding_id"]:
                    item["route"] = row.get("route")
                    item["disposition"] = row.get("disposition")
        global_blockers = [w for w in routed if w.get("route") == ROUTE_GLOBAL_BLOCKER]
        if global_blockers:
            state["global_release_blockers"] = [w["finding_id"] for w in global_blockers]
        security = [w for w in routed if w.get("route") == ROUTE_SECURITY_PROCESS]
        if security and prod:
            return {
                "STATUS": "PARTIAL_COMPLETE",
                "BUG_BATCH_CLEAR": "NO",
                "PROCESS_HANDOFF": "YES",
                "NEXT_COMMAND": PROCESS_ROUTE,
                "SECURITY_FINDINGS": [w["finding_id"] for w in security],
                "MISSION_ID": mission_id,
            }
        _advance_to(repo_root, mission_id, state, Stage.S3)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S3.value,
            "NEXT_STAGE": Stage.S3_5.value,
            "ROUTED_ITEMS": routed,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S3_5:
        routed = state.get("routed_work_items", state.get("work_items", []))
        delta_map = request.get("worktree_delta_map")
        dirty_paths = request.get("dirty_paths")
        if not isinstance(delta_map, dict):
            delta_map = None
        if not isinstance(dirty_paths, list):
            dirty_paths = None
        reconcile = reconcile_worktree_ownership(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            work_items=routed,
            delta_map=delta_map,
            dirty_paths=dirty_paths,
        )
        if reconcile.get("STATUS") == "BLOCKED_WORKTREE_OWNERSHIP":
            return {
                "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                "REASON": reconcile.get("REASON", "worktree_ownership"),
                "MISSION_ID": mission_id,
                "STAGE": Stage.S3_5.value,
            }
        state["worktree_ownership_receipts"] = reconcile.get("RECEIPTS", [])
        _advance_to(repo_root, mission_id, state, Stage.S3_5)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S3_5.value,
            "NEXT_STAGE": Stage.S4.value,
            "WORKTREE_OWNERSHIP": reconcile.get("STATUS", "BOUND"),
            "WORKTREE_RECEIPTS": reconcile.get("RECEIPTS", []),
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S4:
        repair_items = [
            w
            for w in state.get("routed_work_items", state.get("work_items", []))
            if _s4_repair_route(w.get("route"), w)
        ]
        ok, reason, missing = require_receipts_for_s4(repo_root, mission_id, repair_items)
        if repair_items and not ok:
            return {
                "STATUS": "BLOCKED_WORKTREE_OWNERSHIP",
                "REASON": reason,
                "MISSING_RECEIPTS": missing,
                "MISSION_ID": mission_id,
                "STAGE": Stage.S4.value,
            }
        team_receipts = []
        for item in state.get("routed_work_items", state.get("work_items", [])):
            route = item.get("route")
            if route in {ROUTE_NON_REPAIR, ROUTE_TECH_DEBT, ROUTE_GLOBAL_BLOCKER}:
                team_receipts.append({"STATUS": "SKIPPED_NON_REPAIR", "FINDING_ID": item["finding_id"], "ROUTE": route})
                continue
            if not _s4_repair_route(route, item):
                team_receipts.append({"STATUS": "SKIPPED_NON_REPAIR", "FINDING_ID": item["finding_id"], "ROUTE": route})
                continue
            s4_request = dict(request)
            if item.get("member_ids"):
                s4_request["member_ids"] = item["member_ids"]
            s4_request["disposition_id"] = str(item.get("disposition") or item["finding_id"])
            outcome = bug_team_authority.run_s4(
                repo_root,
                s4_request,
                case_id=item["case_id"],
                finding_id=item["finding_id"],
                plan_id=item.get("plan_id", ""),
                work_item_id=item.get("work_item_id", ""),
                production=prod,
            )
            team_receipts.append(outcome)
            if outcome.get("STATUS") == "DISPATCH_REQUIRED":
                return {
                    **outcome,
                    "STATUS": "DISPATCH_REQUIRED",
                    "MISSION_ID": mission_id,
                    "STAGE": Stage.S4.value,
                    "BUG_TEAM_RECEIPTS": team_receipts,
                }
            if outcome.get("STATUS") not in {"READY_FOR_CLOSURE", "STAGE_ADVANCED"}:
                return {**outcome, "MISSION_ID": mission_id, "STAGE": Stage.S4.value}
        _advance_to(repo_root, mission_id, state, Stage.S4)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S4.value,
            "NEXT_STAGE": Stage.S5A.value,
            "BUG_TEAM_RECEIPTS": team_receipts,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S5A:
        for item in state.get("work_items", []):
            if not _s4_repair_route(item.get("route"), item):
                continue
            packet = (
                repo_root
                / "opsx"
                / "runs"
                / "bug-cases"
                / item["case_id"]
                / "results"
                / f"BUG-TEAM-{item['finding_id']}.json"
            )
            if not packet.is_file():
                return {
                    "STATUS": "FAIL_CLOSED",
                    "REASON": "closure_eval_missing_bug_team",
                    "FINDING_ID": item["finding_id"],
                    "MISSION_ID": mission_id,
                }
        _advance_to(repo_root, mission_id, state, Stage.S5A)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S5A.value,
            "NEXT_STAGE": Stage.S5B.value,
            "CLOSURE_EVAL": "PASS",
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S5B:
        close_receipts = []
        for item in state.get("work_items", []):
            if not _s4_repair_route(item.get("route"), item):
                close_receipts.append({"STATUS": "SKIPPED_NON_REPAIR", "FINDING_ID": item["finding_id"]})
                continue
            close_receipts.append(
                materialize_bug_queue.mode_update_close_fixed(
                    ctx,
                    case_id=item["case_id"],
                    finding_id=item["finding_id"],
                )
            )
        _advance_to(repo_root, mission_id, state, Stage.S5B)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S5B.value,
            "NEXT_STAGE": Stage.S6.value,
            "CLOSE_RECEIPTS": close_receipts,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S6:
        receipts = []
        for item in state.get("routed_work_items", state.get("work_items", [])):
            route = item.get("route")
            if route == ROUTE_NON_REPAIR:
                disp = str(item.get("disposition") or "")
                if disp.startswith("FALSE_POSITIVE"):
                    receipts.append(
                        materialize_bug_queue.mode_update_false_positive(
                            ctx, case_id=item["case_id"], finding_id=item["finding_id"]
                        )
                    )
                else:
                    receipts.append(
                        materialize_bug_queue.mode_update_defer(
                            ctx, case_id=item["case_id"], finding_id=item["finding_id"]
                        )
                    )
            elif route == ROUTE_SECURITY_PROCESS:
                receipts.append(
                    materialize_bug_queue.mode_update_security_route(
                        ctx, case_id=item["case_id"], finding_id=item["finding_id"]
                    )
                )
            elif route == ROUTE_GLOBAL_BLOCKER:
                receipts.append(
                    materialize_bug_queue.mode_update_global_release_blocker(
                        ctx, case_id=item["case_id"], finding_id=item["finding_id"]
                    )
                )
        non_repair = [
            w
            for w in state.get("routed_work_items", [])
            if w.get("route")
            in {ROUTE_NON_REPAIR, ROUTE_SECURITY_PROCESS, ROUTE_GLOBAL_BLOCKER, ROUTE_TECH_DEBT}
        ]
        _advance_to(repo_root, mission_id, state, Stage.S6)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S6.value,
            "NEXT_STAGE": Stage.S7.value,
            "NON_REPAIR_RECEIPTS": receipts,
            "NON_REPAIR_RECORDED": [w["finding_id"] for w in non_repair],
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S7:
        evolution_items = [
            w
            for w in state.get("routed_work_items", state.get("work_items", []))
            if w.get("route") in {ROUTE_TECH_DEBT, ROUTE_MUST_FIX_REPAIR, ROUTE_MIXED_SPLIT}
        ]
        queue = evolution_handoff.write_evolution_queue(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
            work_items=evolution_items,
        )
        _advance_to(repo_root, mission_id, state, Stage.S7)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S7.value,
            "NEXT_STAGE": Stage.S8.value,
            "EVOLUTION_QUEUE": queue,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S8:
        handoff = evolution_handoff.write_evolution_handoff(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
        )
        _advance_to(repo_root, mission_id, state, Stage.S8)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S8.value,
            "NEXT_STAGE": Stage.S9.value,
            "EVOLUTION_HANDOFF": handoff,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S9:
        debt = evolution_handoff.write_debt_board_verified(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
        )
        if debt.get("STATUS") in {"FAIL_CLOSED", "PAUSE"}:
            return {**debt, "MISSION_ID": mission_id, "STAGE": Stage.S9.value}
        _advance_to(repo_root, mission_id, state, Stage.S9)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S9.value,
            "NEXT_STAGE": Stage.S10.value,
            "DEBT_BOARD": debt,
            "MISSION_ID": mission_id,
        }

    if stage == Stage.S10:
        work_items = state.get("work_items", [])
        verdict = batch_clear.verify_batch_clear(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
            finding_ids=[w["finding_id"] for w in work_items],
            work_items=work_items,
            production=prod,
        )
        if verdict.get("BUG_BATCH_CLEAR") == "YES":
            _advance_to(repo_root, mission_id, state, Stage.S10)
            return {
                **verdict,
                "STATUS": "STAGE_ADVANCED",
                "STAGE": Stage.S10.value,
                "NEXT_STAGE": Stage.S11.value,
                "MISSION_ID": mission_id,
            }
        return {**verdict, "MISSION_ID": mission_id}

    if stage == Stage.S11:
        path = repo_root / "opsx/runs/orch-missions" / mission_id / "return-to-caller.json"
        path.write_text(
            json.dumps(
                {
                    "mission_id": mission_id,
                    "execution_id": ctx.execution_id,
                    "return_to_caller": "YES",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _advance_to(repo_root, mission_id, state, Stage.S11)
        return {
            "STATUS": "STAGE_ADVANCED",
            "STAGE": Stage.S11.value,
            "NEXT_STAGE": Stage.DONE.value,
            "RETURN_TO_CALLER": "YES",
            "MISSION_ID": mission_id,
        }

    if stage == Stage.DONE:
        work_items = state.get("work_items", [])
        verdict = batch_clear.verify_batch_clear(
            repo_root,
            mission_id=mission_id,
            execution_id=ctx.execution_id,
            head=ctx.head,
            finding_ids=[w["finding_id"] for w in work_items],
            work_items=work_items,
            production=prod,
        )
        if verdict.get("BUG_BATCH_CLEAR") != "YES":
            return {**verdict, "MISSION_ID": mission_id, "REASON": "stale_revision_or_execution"}
        return {
            "STATUS": "BUG_BATCH_CLEAR",
            "BUG_BATCH_CLEAR": "YES",
            "MISSION_ID": mission_id,
            "IDEMPOTENT": "YES",
            "RETURN_TO_CALLER": "YES",
        }

    return {"STATUS": "FAIL_CLOSED", "REASON": f"unknown_stage:{stage}"}


def run_to_batch_clear(repo_root: Path, request: dict[str, Any], *, max_steps: int = 32) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    for _ in range(max_steps):
        step = advance(repo_root, request)
        history.append(step)
        if step.get("BUG_BATCH_CLEAR") == "YES":
            return {
                "STATUS": "BUG_BATCH_CLEAR",
                "BUG_BATCH_CLEAR": "YES",
                "MISSION_ID": request.get("mission"),
                "STEPS": history,
            }
        if step.get("STATUS") in {
            "FAIL_CLOSED",
            "PARTIAL_COMPLETE",
            "BLOCKED_NEEDS_INPUT",
            "DISPATCH_REQUIRED",
        }:
            return {**step, "STEPS": history}
        if step.get("STATUS") != "STAGE_ADVANCED":
            return {**step, "STEPS": history}
    return {"STATUS": "FAIL_CLOSED", "REASON": "max_steps", "STEPS": history}


def execute_auto_persist(repo_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Map legacy auto_persist_command tokens to native stages (no stage jumping)."""

    command = str(request.get("auto_persist_command") or "")
    if not command:
        return {"STATUS": "NOOP"}
    expected = auto_persist_stage_for_command(command)
    if expected is None:
        return {"STATUS": "FAIL_CLOSED", "REASON": "unsupported_auto_persist"}
    mission_id = str(request.get("mission") or "")
    state = load_state(repo_root, mission_id)
    current = Stage(state.get("stage", Stage.S0.value))
    if current != expected:
        return {
            "STATUS": "FAIL_CLOSED",
            "REASON": "auto_persist_stage_mismatch",
            "EXPECTED_STAGE": expected.value,
            "CURRENT_STAGE": current.value,
        }
    allowed, reason = ssca_allows_advance(state, expected)
    if not allowed:
        return {"STATUS": "FAIL_CLOSED", "REASON": reason}
    return advance(repo_root, request)


def resume_after_interrupt(repo_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Idempotent replay from persisted runtime_state."""

    try:
        return run_to_batch_clear(repo_root, request)
    except MaterializeError as error:
        return {"STATUS": "FAIL_CLOSED", "REASON": error.reason}
