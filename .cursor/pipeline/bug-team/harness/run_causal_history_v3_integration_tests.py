# -*- coding: utf-8 -*-
"""Synthetic-case E2E tests for Bug History V3 integration."""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.causal_history.appender import CausalHistoryAppender, append_from_hop_finalize
from runtime.causal_history.config import CausalHistorySettings, load_settings, write_settings
from runtime.causal_history.errors import HistoryPause
from runtime.causal_history.store import CausalEventStore

HARNESS = Path(".tmp/opsx-bug-team-harness/causal-history-v3-integration")
Result = Tuple[bool, str, str]


def _case(name: str) -> Path:
    p = HARNESS / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    (p / "results").mkdir(parents=True, exist_ok=True)
    (p / "team" / "packets").mkdir(parents=True, exist_ok=True)
    return p


def test_legacy_default_skips() -> Result:
    case = _case("legacy-skip")
    (case / "LEDGER.md").write_text("# case\n", encoding="utf-8")
    r = append_from_hop_finalize(
        case_root=case,
        case_id="CASE-LEG",
        worker_id="GREEN_VERIFIER",
        worker_execution_id="WEX/green/1",
        finding_id="F1",
        intent={"CASE_ID": "CASE-LEG", "ORCHESTRATION_ID": "O1", "CHECKPOINT_GENERATION": 1},
        out_pkt={"STATUS": "PASS", "TERMINAL_STATUS": "PASS"},
        artifact_paths=["LEDGER.md"],
    )
    ok = not r.appended and r.skipped_reason == "LIVE_V3_APPEND_DISABLED"
    return ok, "legacy default skips append", "CAUSAL_LEGACY"


def test_v3_append_and_replay() -> Result:
    case = _case("v3-append")
    write_settings(case, CausalHistorySettings.synthetic_v3())
    green = case / "results" / "GREEN-F1.md"
    green.write_text("GREEN evidence\n", encoding="utf-8")
    r = append_from_hop_finalize(
        case_root=case,
        case_id="CASE-V3",
        worker_id="GREEN_VERIFIER",
        worker_execution_id="WEX/green/2",
        finding_id="F1",
        intent={"CASE_ID": "CASE-V3", "ORCHESTRATION_ID": "O1", "CHECKPOINT_GENERATION": 2},
        out_pkt={
            "STATUS": "PASS",
            "TERMINAL_STATUS": "PASS",
            "DEFECT_LOCUS": "app/foo.py",
            "REJECTED_DIAGNOSES": [{"id": "d1", "reason": "wrong layer"}],
        },
        artifact_paths=["results/GREEN-F1.md"],
        provenance={
            "ORIGIN_CHANGE_ID": "chg-origin",
            "DISCOVERY_CHANGE_ID": "chg-disc",
            "AFFECTED_CHANGE_IDS": ["chg-a"],
            "CURRENT_WORK_CHANGE_ID": "chg-work",
        },
    )
    store = CausalEventStore(case, "CASE-V3")
    replay = store.replay_deterministic()
    ok = (
        r.appended
        and r.event_id is not None
        and len(replay) == 1
        and replay[0]["EVENT_KIND"] == "GREEN_CONFIRMED"
        and replay[0]["ORIGIN_CHANGE_ID"] == "chg-origin"
    )
    return ok, f"appended={r.appended} events={len(replay)}", "CAUSAL_V3_APPEND"


def test_history_only_retry_preserves_worker() -> Result:
    case = _case("history-retry")
    write_settings(case, CausalHistorySettings.synthetic_v3())
    proof = case / "results" / "RED-F1.md"
    proof.write_text("red\n", encoding="utf-8")
    worker_runs = case / "proof" / "worker.count"
    worker_runs.parent.mkdir(parents=True, exist_ok=True)
    worker_runs.write_text("1", encoding="utf-8")

    appender = CausalHistoryAppender(case, "CASE-RETRY")
    request = {
        "OPERATION_ID": "WEX/red/1:CAUSAL_EVENT",
        "EVENT_SLOT": "CAUSAL_EVENT",
        "EVENT_BODY": {
            "EVENT_KIND": "RED_ATTEMPT_RECORDED",
            "WORKER_ID": "RED_TEST_WORKER",
        },
        "EVIDENCE_BINDING": {
            "proof_path": "results/RED-F1.md",
            "proof_digest": __import__("hashlib").sha256(proof.read_bytes()).hexdigest(),
            "execution_revision": "rev-1",
        },
    }
    appender.write_append_request(request, "WEX/red/1")
    result = appender.history_only_retry(request)
    ok = result.event_id and worker_runs.read_text() == "1"
    return bool(ok), "history-only retry without worker increment", "CAUSAL_RETRY"


def test_append_failure_pending_not_repeat_worker() -> Result:
    case = _case("append-fail")
    write_settings(case, CausalHistorySettings.synthetic_v3())
    r = append_from_hop_finalize(
        case_root=case,
        case_id="CASE-FAIL",
        worker_id="CLASSIFIER",
        worker_execution_id="WEX/cl/1",
        finding_id="F1",
        intent={"CASE_ID": "CASE-FAIL", "ORCHESTRATION_ID": "O1", "CHECKPOINT_GENERATION": 1},
        out_pkt={"STATUS": "PASS"},
        artifact_paths=[],
    )
    pending = case / "results" / "causal" / "HISTORY_PENDING.json"
    ok = r.history_pending and pending.is_file()
    return ok, f"pending={pending.is_file()}", "CAUSAL_PENDING"


def test_fs_boundary_appender_allowlist() -> Result:
    from runtime import fs_boundary

    diff = fs_boundary.FsDiff(
        created=["results/causal/events/abc.json", "results/causal/acks/op.json"],
        modified=[],
        deleted=[],
    )
    res, viol = fs_boundary.evaluate_write_boundary("CAUSAL_HISTORY_APPENDER", diff)
    ok = res == "PASS" and not viol
    return ok, f"boundary={res}", "FS_BOUNDARY"


def main() -> int:
    tests: List[Tuple[str, Callable[[], Result]]] = [
        ("T1", test_legacy_default_skips),
        ("T2", test_v3_append_and_replay),
        ("T3", test_history_only_retry_preserves_worker),
        ("T4", test_append_failure_pending_not_repeat_worker),
        ("T5", test_fs_boundary_appender_allowlist),
    ]
    evidence_dir = Path("/opt/cursor/artifacts/bug-history-v3-integration")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    failed = 0
    for tid, fn in tests:
        try:
            ok, detail, tag = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail, tag = False, traceback.format_exc(), "EXCEPTION"
        if not ok:
            failed += 1
        rows.append({"id": tid, "passed": ok, "detail": detail, "tag": tag})
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail}")

    report = {
        "suite": "causal_history_v3_integration",
        "head": __import__("subprocess").check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parents[1], text=True
        ).strip(),
        "results": rows,
        "status": "CODE_INTEGRATED_TESTED" if failed == 0 else "FAILED",
    }
    (evidence_dir / "integration_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
