"""Target-native adapters for /opsx-orch-bugbot-converge and /opsx-orch-bug-fix.

These helpers do not orchestrate Cursor hops and do not start a second
asynchronous mission transport. They parse Owner tokens, reuse Temporal
finding_filter + kernel constants, and map machine outcomes to the SOURCE
user-contract statuses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from config import MAX_REPAIR_ROUNDS, MAX_REVIEW_RUNS
from finding_filter import partition_repair_findings
from loop_engine import ReviewRepairLoop
from model_config import ALLOWED_TIERS

DEFAULT_TIER = "medium"
DEFAULT_MAX_CYCLES_SOURCE = 5
PRODUCT_REPAIR_COMMAND = "/opsx-bug-team"
PROCESS_REPAIR_COMMAND = "/opsx-meta-fix-command-defects"
INTAKE_COMMAND = "/opsx-bug-intake-clear"
FAMILY_PLAN_COMMAND = "/opsx-bug-family-plan"
DISPOSITION_COMMAND = "/opsx-bug-disposition"
MATERIALIZE_COMMAND = "/opsx-materialize-bug-queue"
CONVERGE_RUNTIME = "python tools/temporal/runtime.py run bugbot"

_TOKEN_RE = re.compile(r"^(?P<key>[A-Za-z_]+)=(?P<value>.+)$")


class OrchTargetError(ValueError):
    """Fail-closed adapter error with a machine status."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _parse_tokens(raw: str) -> list[str]:
    return [part for part in raw.replace("\r", " ").split() if part]


def parse_converge_invocation(raw_arguments: str) -> dict[str, Any]:
    """Parse /opsx-orch-bugbot-converge $ARGUMENTS into a Temporal run plan."""

    tokens = _parse_tokens(raw_arguments)
    against: str | None = None
    max_cycles: int | None = None
    change: str | None = None
    positional: list[str] = []

    for token in tokens:
        matched = _TOKEN_RE.match(token)
        if matched:
            key = matched.group("key").lower()
            value = matched.group("value").strip()
            if not value:
                raise OrchTargetError("FAIL_CLOSED", f"empty value for {key}=")
            if key == "against":
                against = value
            elif key == "max_cycles":
                if not value.isdigit() or int(value) < 1:
                    raise OrchTargetError("FAIL_CLOSED", "max_cycles must be a positive integer")
                max_cycles = int(value)
            elif key == "change":
                change = value
            elif key == "mission":
                raise OrchTargetError(
                    "BLOCKED_MISSION_RESUME_UNSUPPORTED",
                    "SOURCE mission= resume would require a second orch-missions transport; "
                    "Temporal singleton workflow_id resume is not wired. Owner must re-invoke "
                    "without mission= or extend the Temporal kernel.",
                )
            elif key == "head":
                raise OrchTargetError("FAIL_CLOSED", "head is always the current git branch")
            else:
                raise OrchTargetError("FAIL_CLOSED", f"unknown token {key}=")
            continue
        if token.upper() == "GO":
            continue
        positional.append(token.lower())

    if against is None:
        raise OrchTargetError("FAIL_CLOSED", "against=<frozen-target> is required")

    review = DEFAULT_TIER
    repair = DEFAULT_TIER
    if len(positional) == 2:
        if positional[0] not in ALLOWED_TIERS or positional[1] not in ALLOWED_TIERS:
            raise OrchTargetError("FAIL_CLOSED", "model tiers must be low|medium|high")
        review, repair = positional
    elif positional:
        raise OrchTargetError(
            "FAIL_CLOSED",
            "optional model tiers are the last two tokens: <review-tier> <repair-tier>",
        )

    if max_cycles is not None and max_cycles != MAX_REPAIR_ROUNDS:
        raise OrchTargetError(
            "BLOCKED_MAX_CYCLES_KERNEL_FIXED",
            (
                f"Owner max_cycles={max_cycles} cannot be enforced by the Temporal "
                f"kernel (MAX_REPAIR_ROUNDS={MAX_REPAIR_ROUNDS}, MAX_REVIEW_RUNS="
                f"{MAX_REVIEW_RUNS}). Omit max_cycles (target default {MAX_REPAIR_ROUNDS}) "
                f"or pass max_cycles={MAX_REPAIR_ROUNDS}, or authorize a kernel override."
            ),
        )
    requested_cycles = MAX_REPAIR_ROUNDS

    argv = [
        "run",
        "bugbot",
        "--repo",
        "<workspace>",
        "--base",
        against,
        "--review",
        review,
        "--repair",
        repair,
    ]
    return {
        "status": "PLAN_READY",
        "command": "/opsx-orch-bugbot-converge",
        "transport": "temporal-cursor-sdk-runtime-v1",
        "pipeline": "bugbot",
        "against": against,
        "base": against,
        "change": change,
        "max_cycles": requested_cycles,
        "max_bugbot_checks": requested_cycles + 1,
        "review_tier": review,
        "repair_tier": repair,
        "runtime_argv": argv,
        "runtime_command": CONVERGE_RUNTIME,
        "inner_review_command": "/review-bugbot",
        "inner_repair_command": "/opsx-orch-bug-fix",
        "product_mutation_command": PRODUCT_REPAIR_COMMAND,
        "process_repair_command": PROCESS_REPAIR_COMMAND,
        "wrapper_git_write": False,
        "wrapper_product_write": False,
        "start_temporal_from_bug_fix": False,
    }


def map_temporal_converge_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a Temporal loop outcome onto the converge user-contract statuses."""

    status = str(payload.get("status") or "").upper()
    last_review = payload.get("last_review") or {}
    excluded = list(last_review.get("excluded_findings") or payload.get("excluded_findings") or [])
    repairable = list(last_review.get("repairable_findings") or payload.get("repairable_findings") or [])
    fingerprint = last_review.get("fingerprint") or payload.get("fingerprint")
    candidate_sha = last_review.get("candidate_sha") or payload.get("candidate_sha")
    review_runs = payload.get("review_runs")
    repair_rounds = payload.get("repair_rounds")

    mapped = {
        "COMMAND": "/opsx-orch-bugbot-converge",
        "KERNEL_STATUS": status,
        "TARGET_FROZEN": payload.get("base") or payload.get("against"),
        "REPAIR_CYCLE": repair_rounds,
        "BUGBOT_CHECK_INDEX": review_runs,
        "FINDING_FINGERPRINT": fingerprint,
        "CANDIDATE_SHA": candidate_sha,
        "EXCLUDED_PROCESS_FINDINGS": len(excluded),
        "REPAIRABLE_FINDINGS": len(repairable),
        "BUGBOT_CONVERGED": "NO",
        "EVIDENCE_BOUND": bool(candidate_sha or fingerprint) or last_review.get("clean") is True,
    }

    if status in {"BUGBOT_CLEAN"}:
        if excluded and not repairable:
            mapped.update(
                {
                    "STATUS": "PROCESS_HANDOFF",
                    "REASON": "Native review is product-lane clean only; process findings remain.",
                    "NEXT_REAL_ACTION": PROCESS_REPAIR_COMMAND,
                }
            )
            return mapped
        if not mapped["EVIDENCE_BOUND"] or last_review.get("clean") is False:
            mapped.update(
                {
                    "STATUS": "CHECK_ERROR",
                    "REASON": "BUGBOT_CLEAN without freshness-bound last_review evidence.",
                }
            )
            return mapped
        mapped.update(
            {
                "STATUS": "BUGBOT_CONVERGED",
                "BUGBOT_CONVERGED": "YES",
                "FINAL_BUGBOT_RESULT": "CLEAR",
                "ACTIONABLE_FINDINGS": 0,
            }
        )
        return mapped

    mapping = {
        "MAX_ROUNDS_REACHED": ("ITERATION_LIMIT_REACHED", "Kernel check/repair budget exhausted."),
        "STALLED": ("BLOCKED_NO_PROGRESS", "Actionable findings fingerprint unchanged."),
        "NO_REPAIR_PROGRESS": ("BLOCKED_NO_PROGRESS", "Repair hop reported nothing repaired."),
        "REVIEW_FAILED": ("CHECK_ERROR", "Native /review-bugbot did not complete."),
        "REPAIR_FAILED": ("REPAIR_BLOCKED", "Nested /opsx-orch-bug-fix did not terminalize."),
        "REPAIR_COMPLETION_UNCERTAIN": ("REPAIR_BLOCKED", "Repair hop ended ambiguously."),
        "REPAIR_BLOCKED": (
            "REPAIR_BLOCKED",
            "Repair orch reported a blocking terminal; wrapper stopped before materialize.",
        ),
        "PARTIAL_COMPLETE": (
            "BLOCKED_NO_PROGRESS",
            "Repair orch PARTIAL_COMPLETE; wrapper stopped before materialize.",
        ),
        "FAIL_CLOSED": (
            "FAIL_CLOSED",
            "Repair orch FAIL_CLOSED; wrapper stopped before materialize.",
        ),
        "COMMIT_MATERIALIZE_BLOCKED": (
            "REPAIR_BLOCKED",
            "Authorized /opsx-commit-all materialization failed; wrapper must not git-write.",
        ),
        "TEMPORAL_SERVER_UNAVAILABLE": ("FAIL_CLOSED", "Temporal transport unavailable."),
    }
    if status in mapping:
        out_status, reason = mapping[status]
        mapped.update({"STATUS": out_status, "REASON": reason})
        return mapped

    mapped.update(
        {
            "STATUS": "FAIL_CLOSED",
            "REASON": f"unmapped Temporal status {status or 'MISSING'}",
        }
    )
    return mapped


def parse_bug_fix_invocation(raw_arguments: str) -> dict[str, Any]:
    """Parse /opsx-orch-bug-fix $ARGUMENTS. Does not start Temporal."""

    tokens = _parse_tokens(raw_arguments)
    change: str | None = None
    has_inline_findings = False
    mission_go = False
    mission_id: str | None = None
    native_mission = False

    for token in tokens:
        matched = _TOKEN_RE.match(token)
        if matched:
            key = matched.group("key").lower()
            value = matched.group("value").strip()
            if key == "change":
                change = value
            elif key in {"findings", "hunt_findings", "clean_findings"}:
                has_inline_findings = True
            elif key == "mission":
                mission_id = value
            elif key == "native_mission" and value.upper() == "YES":
                native_mission = True
            else:
                raise OrchTargetError("FAIL_CLOSED", f"unknown token {key}=")
            continue
        if token.upper() == "GO":
            mission_go = True
            continue
        if token.endswith(":") and token.upper() in {
            "HUNT_FINDINGS:",
            "CLEAN_FINDINGS:",
            "FINDINGS:",
        }:
            has_inline_findings = True

    source = "R1_INLINE" if has_inline_findings else "R2_IMMEDIATE_PRIOR_OR_R3"
    resume_native = bool(mission_id)
    if resume_native:
        native_mission = True
    plan: dict[str, Any] = {
        "status": "PLAN_READY",
        "command": "/opsx-orch-bug-fix",
        "change": change,
        "mission": mission_id,
        "mission_go": mission_go,
        "source_precedence": source,
        "start_temporal": False,
        "wrapper_product_write": False,
        "wrapper_git_write": False,
        "native_mission": "YES" if native_mission else "NO",
        "resume_mode": "RUNTIME_STATE" if resume_native else "FRESH",
        "product_mutation_command": PRODUCT_REPAIR_COMMAND,
        "process_repair_command": PROCESS_REPAIR_COMMAND,
        "sequence": [
            INTAKE_COMMAND,
            FAMILY_PLAN_COMMAND,
            DISPOSITION_COMMAND,
            PRODUCT_REPAIR_COMMAND,
            MATERIALIZE_COMMAND,
        ],
        "family_plan_persist": False,
        "disposition_persist": False,
        "durable_close_command": MATERIALIZE_COMMAND,
    }
    if resume_native:
        plan["sequence"] = ["/opsx-orch-bug-fix native_mission=YES mission resume"]
        plan["transport_note"] = (
            "Resume via in-parent runtime_state.json + orch_mission_runtime.resume_after_interrupt"
        )
    return plan


def classify_findings(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Split findings with the Temporal product allowlist. Wrapper does not repair."""

    repairable, excluded = partition_repair_findings(rows)
    return {
        "PRODUCT_BUG_FINDINGS": repairable,
        "OPSX_COMMAND_CONTRACT_DEFECTS": excluded,
        "PROCESS_FINDINGS_ROUTED_COUNT": len(excluded),
        "PRODUCT_COUNT": len(repairable),
        "PROCESS_REPAIR_COMMAND": PROCESS_REPAIR_COMMAND,
        "PRODUCT_REPAIR_COMMAND": PRODUCT_REPAIR_COMMAND,
    }


def evaluate_bug_fix_batch(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Deterministic S0 gate for one admitted batch."""

    classified = classify_findings(rows)
    product = classified["PRODUCT_COUNT"]
    process_n = classified["PROCESS_FINDINGS_ROUTED_COUNT"]
    if product == 0 and process_n == 0:
        return {
            **classified,
            "STATUS": "BUG_BATCH_CLEAR",
            "BUG_BATCH_CLEAR": "YES",
            "REPAIRED_THIS_BATCH": "NO",
            "FIX_NOW": "(none)",
        }
    if product == 0 and process_n > 0:
        return {
            **classified,
            "STATUS": "PARTIAL_COMPLETE",
            "BUG_BATCH_CLEAR": "NO",
            "REPAIRED_THIS_BATCH": "NO",
            "PROCESS_HANDOFF": "YES",
            "FIX_NOW": "(none)",
            "NEXT_REAL_ACTION": PROCESS_REPAIR_COMMAND,
        }
    return {
        **classified,
        "STATUS": "ACTIVE",
        "BUG_BATCH_CLEAR": "NO",
        "REPAIRED_THIS_BATCH": None,
        "NEXT_REAL_ACTION": PRODUCT_REPAIR_COMMAND,
    }


def should_stop_repeated_causal_finding(
    *,
    previous_fingerprint: str | None,
    current_fingerprint: str | None,
    repair_rounds: int,
    consecutive_same_fp_reviews: int,
    sibling_expansion: bool = False,
) -> dict[str, Any]:
    """Mandatory stop: same unresolved fingerprint, stall, or sibling fan-out."""

    last_fp, consecutive = ReviewRepairLoop.update_stagnation_state(
        current_fingerprint,
        last_fp=previous_fingerprint,
        consecutive_same_fp_reviews=consecutive_same_fp_reviews,
    )
    stalled = ReviewRepairLoop.is_stalled(repair_rounds, consecutive)
    same_unresolved = bool(
        previous_fingerprint
        and current_fingerprint
        and previous_fingerprint == current_fingerprint
        and repair_rounds >= 1
    )
    stop = stalled or sibling_expansion or same_unresolved
    status = "BLOCKED_NO_PROGRESS" if stop else "CONTINUE"
    reason = "none"
    if sibling_expansion:
        reason = "Sibling expansion must not continue this batch."
    elif stalled or same_unresolved:
        reason = "Same unresolved causal finding fingerprint; stop FAIL/PAUSE."
    return {
        "STATUS": status,
        "STOP": stop,
        "fingerprint": last_fp,
        "consecutive_same_fp_reviews": consecutive,
        "REASON": reason,
    }


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("converge-plan")
    p_plan.add_argument("--args", default="")

    p_map = sub.add_parser("converge-map")
    p_map.add_argument("--outcome-json", required=True)

    p_fix = sub.add_parser("bug-fix-plan")
    p_fix.add_argument("--args", default="")

    p_eval = sub.add_parser("bug-fix-eval")
    p_eval.add_argument("--findings-json", required=True)

    p_stop = sub.add_parser("stop-eval")
    p_stop.add_argument("--previous-fingerprint", default=None)
    p_stop.add_argument("--current-fingerprint", default=None)
    p_stop.add_argument("--repair-rounds", type=int, default=0)
    p_stop.add_argument("--consecutive-same-fp", type=int, default=0)
    p_stop.add_argument("--sibling-expansion", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "converge-plan":
            return _print(parse_converge_invocation(args.args))
        if args.cmd == "converge-map":
            payload = json.loads(args.outcome_json)
            if not isinstance(payload, dict):
                raise OrchTargetError("FAIL_CLOSED", "outcome JSON must be an object")
            return _print(map_temporal_converge_outcome(payload))
        if args.cmd == "bug-fix-plan":
            return _print(parse_bug_fix_invocation(args.args))
        if args.cmd == "bug-fix-eval":
            rows = json.loads(args.findings_json)
            if not isinstance(rows, list):
                raise OrchTargetError("FAIL_CLOSED", "findings JSON must be a list")
            return _print(evaluate_bug_fix_batch(rows))
        if args.cmd == "stop-eval":
            return _print(
                should_stop_repeated_causal_finding(
                    previous_fingerprint=args.previous_fingerprint,
                    current_fingerprint=args.current_fingerprint,
                    repair_rounds=args.repair_rounds,
                    consecutive_same_fp_reviews=args.consecutive_same_fp,
                    sibling_expansion=args.sibling_expansion,
                )
            )
    except OrchTargetError as error:
        _print({"STATUS": error.status, "REASON": error.reason, "BUGBOT_CONVERGED": "NO"})
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
