# -*- coding: utf-8 -*-
"""Focused tests: regression history capture for Bug History V3."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT))

from runtime.causal_history.appender import append_from_hop_finalize
from runtime.causal_history.config import CausalHistorySettings, write_settings
from runtime.causal_history.regression_capture import (
    REGRESSION_ASSESSMENT_ABSENT,
    REGRESSION_ASSESSMENT_CONFIRMED,
    REGRESSION_ASSESSMENT_MISSING,
    REGRESSION_ASSESSMENT_NOT_ASSESSED,
)
from runtime.causal_history.store import CausalEventStore

HARNESS = Path(".tmp/opsx-bug-team-harness/regression-capture-v3")
ARTIFACTS = Path("/opt/cursor/artifacts/bug-history-regression-capture")
Result = Tuple[bool, str, str]


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _case(name: str) -> Path:
    p = HARNESS / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    (p / "results").mkdir(parents=True, exist_ok=True)
    (p / "team" / "packets").mkdir(parents=True, exist_ok=True)
    write_settings(p, CausalHistorySettings.synthetic_v3())
    return p


def _append_green(case: Path, out_pkt: dict, *, proof_name: str = "GREEN-F1.md") -> dict:
    proof = case / "results" / proof_name
    if not proof.is_file():
        proof.write_text("green proof\n", encoding="utf-8")
    r = append_from_hop_finalize(
        case_root=case,
        case_id="CASE-REG",
        worker_id="GREEN_VERIFIER",
        worker_execution_id="WEX/green/reg",
        finding_id="F1",
        intent={
            "CASE_ID": "CASE-REG",
            "ORCHESTRATION_ID": "O-REG",
            "CHECKPOINT_GENERATION": 3,
            "WORKER_EXECUTION_ID": "WEX/green/reg",
        },
        out_pkt=out_pkt,
        artifact_paths=[f"results/{proof_name}"],
    )
    store = CausalEventStore(case, "CASE-REG")
    replay = store.replay_deterministic()
    return {"append": r, "event": replay[-1] if replay else {}}


def test_not_assessed_without_fields() -> Result:
    test_id = "REG-CAP-01"
    case = _case("not-assessed")
    res = _append_green(case, {"STATUS": "PASS", "TERMINAL_STATUS": "PASS", "PAYLOAD": {}})
    ev = res["event"]
    ok = (
        res["append"].appended
        and ev.get("REGRESSION_ASSESSMENT") == REGRESSION_ASSESSMENT_NOT_ASSESSED
        and ev.get("REGRESSION_FINDINGS") == []
        and ev.get("REGRESSION_FLAGS") is None
    )
    return ok, str(ev.get("REGRESSION_ASSESSMENT")), test_id


def test_confirmed_regression_with_evidence() -> Result:
    test_id = "REG-CAP-02"
    case = _case("confirmed")
    reg_path = case / "results" / "REGRESSION-F1.md"
    reg_path.write_text("regression: checkout API 500\n", encoding="utf-8")
    digest = hashlib.sha256(reg_path.read_bytes()).hexdigest()
    head = _git_head()
    res = _append_green(
        case,
        {
            "STATUS": "PASS",
            "TERMINAL_STATUS": "PASS",
            "WORKER_EXECUTION_ID": "WEX/green/reg",
            "PAYLOAD": {
                "REGRESSION_ASSESSMENT": REGRESSION_ASSESSMENT_CONFIRMED,
                "REPOSITORY_REVISION": head,
                "REGRESSION_FINDINGS": [
                    {
                        "finding_id": "reg-checkout",
                        "summary": "checkout API returns 500 after fix",
                        "evidence_ref": {"path": "results/REGRESSION-F1.md", "digest": digest},
                    }
                ],
            },
        },
    )
    ev = res["event"]
    findings = ev.get("REGRESSION_FINDINGS") or []
    exec_block = ev.get("REGRESSION_EXECUTION") or {}
    binding = ev.get("EVIDENCE_BINDING") or {}
    ok = (
        ev.get("REGRESSION_ASSESSMENT") == REGRESSION_ASSESSMENT_CONFIRMED
        and len(findings) == 1
        and findings[0]["summary"].startswith("checkout")
        and findings[0]["evidence_ref"]["digest"] == digest
        and exec_block.get("repository_revision") == head
        and binding.get("execution_revision", "").startswith("3:")
    )
    return ok, f"findings={len(findings)} rev={head[:8]}", test_id


def test_absence_confirmed() -> Result:
    test_id = "REG-CAP-03"
    case = _case("absent")
    suite = case / "results" / "REGRESSION-SUITE-F1.json"
    suite.write_text('{"passed": true}\n', encoding="utf-8")
    res = _append_green(
        case,
        {
            "STATUS": "PASS",
            "TERMINAL_STATUS": "PASS",
            "PAYLOAD": {
                "REGRESSION_ASSESSMENT": REGRESSION_ASSESSMENT_ABSENT,
                "REGRESSION_ABSENCE_EVIDENCE": {
                    "path": "results/REGRESSION-SUITE-F1.json",
                    "role": "automated_suite",
                },
            },
        },
    )
    ev = res["event"]
    ok = (
        ev.get("REGRESSION_ASSESSMENT") == REGRESSION_ASSESSMENT_ABSENT
        and ev.get("REGRESSION_ABSENCE_EVIDENCE", {}).get("path") == "results/REGRESSION-SUITE-F1.json"
        and ev.get("REGRESSION_FINDINGS") == []
    )
    return ok, str(ev.get("REGRESSION_ASSESSMENT")), test_id


def test_missing_required_data() -> Result:
    test_id = "REG-CAP-04"
    case = _case("missing")
    res = _append_green(
        case,
        {
            "STATUS": "PASS",
            "TERMINAL_STATUS": "PASS",
            "PAYLOAD": {
                "REGRESSION_ASSESSMENT": REGRESSION_ASSESSMENT_CONFIRMED,
                "REGRESSION_FINDINGS": [],
            },
        },
    )
    ev = res["event"]
    ok = ev.get("REGRESSION_ASSESSMENT") == REGRESSION_ASSESSMENT_MISSING
    return ok, str(ev.get("REGRESSION_ASSESSMENT")), test_id


def test_legacy_flags_map_to_confirmed() -> Result:
    test_id = "REG-CAP-05"
    case = _case("legacy-flags")
    res = _append_green(
        case,
        {
            "STATUS": "PASS",
            "TERMINAL_STATUS": "PASS",
            "PAYLOAD": {"REGRESSION_FLAGS": ["billing totals drift"]},
        },
    )
    ev = res["event"]
    ok = (
        ev.get("REGRESSION_ASSESSMENT") == REGRESSION_ASSESSMENT_CONFIRMED
        and ev.get("REGRESSION_FLAGS") == ["billing totals drift"]
    )
    return ok, str(ev.get("REGRESSION_ASSESSMENT")), test_id


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("REG-CAP-01", test_not_assessed_without_fields),
    ("REG-CAP-02", test_confirmed_regression_with_evidence),
    ("REG-CAP-03", test_absence_confirmed),
    ("REG-CAP-04", test_missing_required_data),
    ("REG-CAP-05", test_legacy_flags_map_to_confirmed),
]


def main() -> int:
    head = _git_head()
    exec_id = f"regression-capture@{head[:12]}"
    rows = []
    failed = 0
    for tid, fn in TESTS:
        try:
            ok, detail, case_id = fn()
            assert case_id == tid
        except Exception:
            ok, detail = False, traceback.format_exc()
        if not ok:
            failed += 1
        rows.append(
            {
                "test_id": tid,
                "passed": ok,
                "detail": detail,
                "execution_id": exec_id,
                "revision": head,
                "command": "python3 harness/run_causal_history_regression_capture_tests.py",
            }
        )
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "PASS" if failed == 0 else "FAIL",
        "execution_id": exec_id,
        "revision": head,
        "tests": rows,
    }
    (ARTIFACTS / "regression_capture_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
