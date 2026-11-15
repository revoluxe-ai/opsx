"""Dedicated tests for target-native orch-bugbot-converge / orch-bug-fix adapters."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from config import MAX_REPAIR_ROUNDS, REPAIR_PROMPT
from orch_target import (
    OrchTargetError,
    classify_findings,
    evaluate_bug_fix_batch,
    map_temporal_converge_outcome,
    parse_bug_fix_invocation,
    parse_converge_invocation,
    should_stop_repeated_causal_finding,
)
from parsers import parse_opsx_bug_fix_result

HERE = Path(__file__).resolve().parent
TEMPORAL = HERE.parent
ROOT = TEMPORAL.parents[1]


def test_converge_plan_maps_against_to_temporal_bugbot():
    plan = parse_converge_invocation("against=stage")
    assert plan["pipeline"] == "bugbot"
    assert plan["base"] == "stage"
    assert plan["max_cycles"] == MAX_REPAIR_ROUNDS
    assert plan["runtime_argv"][:2] == ["run", "bugbot"]
    assert "--base" in plan["runtime_argv"]
    assert plan["wrapper_product_write"] is False
    assert plan["start_temporal_from_bug_fix"] is False
    assert plan["inner_repair_command"] == "/opsx-orch-bug-fix"
    assert plan["product_mutation_command"] == "/opsx-bug-team"


def test_converge_rejects_source_default_max_cycles_without_kernel_cap():
    with pytest.raises(OrchTargetError) as exc:
        parse_converge_invocation("against=stage max_cycles=5")
    assert exc.value.status == "BLOCKED_MAX_CYCLES_KERNEL_FIXED"


def test_converge_rejects_mission_resume():
    with pytest.raises(OrchTargetError) as exc:
        parse_converge_invocation("against=stage mission=ORCH-bugbot-converge-1")
    assert exc.value.status == "BLOCKED_MISSION_RESUME_UNSUPPORTED"


def test_converge_requires_against():
    with pytest.raises(OrchTargetError) as exc:
        parse_converge_invocation("medium low")
    assert exc.value.status == "FAIL_CLOSED"


def test_map_clean_review_is_converged_only_with_evidence():
    mapped = map_temporal_converge_outcome(
        {
            "status": "BUGBOT_CLEAN",
            "repair_rounds": 0,
            "review_runs": 1,
            "last_review": {
                "clean": True,
                "repairable_findings": [],
                "excluded_findings": [],
                "fingerprint": "abc",
                "candidate_sha": "deadbeef",
            },
        }
    )
    assert mapped["STATUS"] == "BUGBOT_CONVERGED"
    assert mapped["BUGBOT_CONVERGED"] == "YES"


def test_map_clean_does_not_pass_on_self_report_without_review_identity():
    mapped = map_temporal_converge_outcome({"status": "BUGBOT_CLEAN"})
    assert mapped["STATUS"] == "CHECK_ERROR"
    assert mapped["BUGBOT_CONVERGED"] == "NO"


def test_map_process_exclusions_are_not_converged():
    mapped = map_temporal_converge_outcome(
        {
            "status": "BUGBOT_CLEAN",
            "last_review": {
                "clean": True,
                "repairable_findings": [],
                "excluded_findings": [{"location": ".cursor/commands/x.md:1"}],
                "fingerprint": "proc",
                "candidate_sha": "sha",
            },
        }
    )
    assert mapped["STATUS"] == "PROCESS_HANDOFF"
    assert mapped["BUGBOT_CONVERGED"] == "NO"
    assert mapped["NEXT_REAL_ACTION"] == "/opsx-meta-fix-command-defects"


@pytest.mark.parametrize(
    ("kernel", "expected"),
    [
        ("STALLED", "BLOCKED_NO_PROGRESS"),
        ("NO_REPAIR_PROGRESS", "BLOCKED_NO_PROGRESS"),
        ("MAX_ROUNDS_REACHED", "ITERATION_LIMIT_REACHED"),
        ("REVIEW_FAILED", "CHECK_ERROR"),
        ("REPAIR_FAILED", "REPAIR_BLOCKED"),
        ("COMMIT_MATERIALIZE_BLOCKED", "REPAIR_BLOCKED"),
    ],
)
def test_map_stop_statuses(kernel: str, expected: str):
    mapped = map_temporal_converge_outcome({"status": kernel, "last_review": {"fingerprint": "f"}})
    assert mapped["STATUS"] == expected
    assert mapped["BUGBOT_CONVERGED"] == "NO"


def test_bug_fix_plan_never_starts_temporal():
    plan = parse_bug_fix_invocation("change=demo GO")
    assert plan["start_temporal"] is False
    assert plan["wrapper_product_write"] is False
    assert plan["product_mutation_command"] == "/opsx-bug-team"
    assert plan["family_plan_persist"] is False
    assert "/opsx-bug-team" in plan["sequence"]


def test_bug_fix_mission_token_plans_native_resume():
    plan = parse_bug_fix_invocation("mission=ORCH-bug-fix-1")
    assert plan["native_mission"] == "YES"
    assert plan["mission"] == "ORCH-bug-fix-1"
    assert plan["resume_mode"] == "RUNTIME_STATE"


def test_s0_process_only_does_not_claim_batch_clear_or_repair():
    result = evaluate_bug_fix_batch(
        [{"severity": "High", "location": ".cursor/commands/opsx.md:1", "finding": "contract"}]
    )
    assert result["STATUS"] == "PARTIAL_COMPLETE"
    assert result["REPAIRED_THIS_BATCH"] == "NO"
    assert result["PROCESS_HANDOFF"] == "YES"
    assert result["PRODUCT_COUNT"] == 0


def test_s0_empty_is_batch_clear_without_self_repair():
    result = evaluate_bug_fix_batch([])
    assert result["STATUS"] == "BUG_BATCH_CLEAR"
    assert result["REPAIRED_THIS_BATCH"] == "NO"


def test_s0_product_routes_to_bug_team():
    result = evaluate_bug_fix_batch(
        [{"severity": "High", "location": "app/foo.py:10", "finding": "null deref"}]
    )
    assert result["STATUS"] == "ACTIVE"
    assert result["NEXT_REAL_ACTION"] == "/opsx-bug-team"
    assert classify_findings(result["PRODUCT_BUG_FINDINGS"])["PRODUCT_COUNT"] == 1


def test_repeated_fingerprint_stops():
    stopped = should_stop_repeated_causal_finding(
        previous_fingerprint="same",
        current_fingerprint="same",
        repair_rounds=1,
        consecutive_same_fp_reviews=1,
    )
    assert stopped["STOP"] is True
    assert stopped["STATUS"] == "BLOCKED_NO_PROGRESS"


def test_sibling_expansion_stops():
    stopped = should_stop_repeated_causal_finding(
        previous_fingerprint="a",
        current_fingerprint="b",
        repair_rounds=1,
        consecutive_same_fp_reviews=0,
        sibling_expansion=True,
    )
    assert stopped["STOP"] is True


def test_repair_parser_accepts_target_bug_fix_envelope():
    raw = """STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
REPAIRED_THIS_BATCH: NO
FIX_NOW:
  (none)
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.bug_batch_clear is True
    assert parsed.repaired_this_batch is False
    assert parsed.uncertain is False


def test_repair_prompt_still_targets_orch_bug_fix():
    assert REPAIR_PROMPT.startswith("/opsx-orch-bug-fix")
    assert "native_mission=YES" in REPAIR_PROMPT


def test_adapter_cli_converge_plan_roundtrip():
    proc = subprocess.run(
        [sys.executable, str(TEMPORAL / "orch_target.py"), "converge-plan", "--args", "against=stage"],
        cwd=str(TEMPORAL),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["against"] == "stage"
