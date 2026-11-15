# -*- coding: utf-8 -*-
"""Real finalize_live_hop E2E for Bug History V3 (isolated synthetic cases)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from runtime import activation, live_task_hop, packets  # noqa: E402
from runtime.causal_history import resolve_pending_history_only  # noqa: E402
from runtime.causal_history.config import CausalHistorySettings, write_settings  # noqa: E402
from runtime.causal_history.errors import HistoryErrorCode, HistoryPause  # noqa: E402
from runtime.causal_history.store import CausalEventStore  # noqa: E402

HARNESS = Path(".tmp/opsx-bug-team-harness/real-hop-causal-v3")
ARTIFACTS = Path("/opt/cursor/artifacts/bug-history-real-hop-e2e")
Result = Tuple[bool, str, str, str]


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(name: str, *, v3: bool = True) -> Path:
    p = HARNESS / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    (p / "team" / "packets").mkdir(parents=True)
    (p / "results").mkdir(parents=True)
    (p / "gates").mkdir(parents=True)
    (p / "LEDGER.md").write_text("# synthetic\n", encoding="utf-8")
    if v3:
        write_settings(p, CausalHistorySettings.synthetic_v3())
    return p


def _out_packet(
    *,
    worker_id: str,
    wex: str,
    orch: str,
    case_id: str,
    finding_id: str,
    status: str = "PASS",
    terminal: str = "PASS",
    packet_generation: int = 1,
    checkpoint_generation: int = 1,
    attempt: int = 1,
    payload: Optional[Dict[str, Any]] = None,
    top_level: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = time.time()
    pkt: Dict[str, Any] = {
        "SCHEMA_VERSION": "1",
        "PACKET_TYPE": "WORKER_OUTPUT",
        "PACKET_ID": f"PKT/{wex}/OUT",
        "PACKET_GENERATION": packet_generation,
        "ORCHESTRATION_ID": orch,
        "WORKER_EXECUTION_ID": wex,
        "WORKER_ID": worker_id,
        "CASE_ID": case_id,
        "FINDING_ID": finding_id,
        "PARENT_PACKET_ID": f"PKT/{wex}/IN",
        "DEPENDENCY_PACKET_IDS": [],
        "DEPENDENCY_HASHES": {},
        "CHECKPOINT_GENERATION": checkpoint_generation,
        "CREATED_AT": now,
        "EXPIRES_AT": now + 3600,
        "STATUS": status,
        "TERMINAL_STATUS": terminal,
        "PAYLOAD": {"PROOF": True, **(payload or {})},
        "ARTIFACTS_WRITTEN": [],
        "EXECUTION_MODE": "REVIEW_PROOF_ONLY",
        "ACTIVATION_STATE_HASH": activation.activation_state_hash(),
        "ATTEMPT": attempt,
    }
    if top_level:
        pkt.update(top_level)
    return pkt


def _run_finalize(
    case: Path,
    *,
    worker_id: str,
    case_id: str,
    finding_id: str,
    orch: str,
    checkpoint_generation: int,
    packet_generation: int,
    attempt: int = 1,
    payload: Optional[Dict[str, Any]] = None,
    top_level: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    before_finalize: Optional[Callable[[Path, Dict[str, Any]], None]] = None,
    payload_after_setup: Optional[Callable[[Path], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    prep = live_task_hop.prepare_live_hop(
        case_root=case,
        orchestration_id=orch,
        worker_id=worker_id,
        case_id=case_id,
        finding_id=finding_id,
        execution_mode="REVIEW_PROOF_ONLY",
        checkpoint_generation=checkpoint_generation,
        packet_generation=packet_generation,
        attempt=attempt,
        proof_root=case,
    )
    live_task_hop.mark_task_started(Path(prep["DISPATCH_INTENT_PATH"]))
    if before_finalize:
        before_finalize(case, prep)
    effective_payload = dict(payload or {})
    if payload_after_setup:
        effective_payload.update(payload_after_setup(case))
    out_path = Path(prep["EXPECTED_OUTPUT_PATH"])
    pkt = _out_packet(
        worker_id=worker_id,
        wex=prep["WORKER_EXECUTION_ID"],
        orch=orch,
        case_id=case_id,
        finding_id=finding_id,
        packet_generation=packet_generation,
        checkpoint_generation=checkpoint_generation,
        attempt=prep["ATTEMPT"],
        payload=effective_payload,
        top_level=top_level,
    )
    packets.write_packet(out_path, pkt)
    intent_path = Path(prep["DISPATCH_INTENT_PATH"])
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    if provenance:
        intent["CHANGE_PROVENANCE"] = provenance
        intent_path.write_text(json.dumps(intent, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return live_task_hop.finalize_live_hop(
        case_root=case,
        dispatch_intent_path=intent_path,
        actual_cursor_agent_id=f"agent-{worker_id}-{attempt}",
        output_packet_path=out_path,
    )


def rh_e2e_01_green_finalize_causal() -> Result:
    test_id = "RH-E2E-01"
    case = _case("rh01")
    fid = "F-RH01"

    def setup(c: Path, _prep: Dict[str, Any]) -> None:
        green = c / "results" / f"GREEN-{fid}.md"
        green.write_text("verified green evidence\n", encoding="utf-8")

    res = _run_finalize(
        case,
        worker_id="GREEN_VERIFIER",
        case_id="CASE-RH01",
        finding_id=fid,
        orch="ORCH/rh01",
        checkpoint_generation=1,
        packet_generation=1,
        payload={"VERIFIED_RESOLUTION": "fix validated by tests"},
        provenance={
            "ORIGIN_CHANGE_ID": "chg-origin",
            "DISCOVERY_CHANGE_ID": "chg-disc",
            "AFFECTED_CHANGE_IDS": ["chg-a"],
            "CURRENT_WORK_CHANGE_ID": "chg-work",
        },
        before_finalize=setup,
    )
    ch = res.get("CAUSAL_HISTORY") or {}
    store = CausalEventStore(case, "CASE-RH01")
    replay = store.replay_deterministic()
    ack = case / "results" / "causal" / "acks" / "WEX_GREEN_VERIFIER__causal_event.json"
    # slug may vary - find ack by glob
    acks = list((case / "results" / "causal" / "acks").glob("*.json"))
    ok = (
        res.get("CHECKPOINT_ADVANCED") is True
        and ch.get("CAUSAL_APPENDED") is True
        and len(replay) == 1
        and replay[0]["EVENT_KIND"] == "GREEN_CONFIRMED"
        and len(acks) == 1
    )
    return ok, f"ch={ch} events={len(replay)}", test_id, str(case / "results" / "causal")


def rh_e2e_02_append_fail_history_retry() -> Result:
    test_id = "RH-E2E-02"
    case = _case("rh02")
    fid = "F-RH02"
    calls = {"n": 0}
    # До hop: запись в team/ во время before_finalize ломает write-boundary GREEN_VERIFIER.
    (case / "team" / "worker_runs.count").write_text("1", encoding="utf-8")

    def setup(c: Path, _p: Dict[str, Any]) -> None:
        (c / "results" / f"GREEN-{fid}.md").write_text("g\n", encoding="utf-8")

    real_publish = CausalEventStore.publish_event

    def flaky_publish(self, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise HistoryPause(HistoryErrorCode.AMBIGUOUS_TMP, "simulated append fail", "retry")
        return real_publish(self, *args, **kwargs)

    with patch.object(CausalEventStore, "publish_event", flaky_publish):
        res = _run_finalize(
            case,
            worker_id="GREEN_VERIFIER",
            case_id="CASE-RH02",
            finding_id=fid,
            orch="ORCH/rh02",
            checkpoint_generation=1,
            packet_generation=1,
            before_finalize=setup,
        )
    ch = res.get("CAUSAL_HISTORY") or {}
    pending = case / "results" / "causal" / "HISTORY_PENDING.json"
    worker_runs = int((case / "team" / "worker_runs.count").read_text())
    ok_after_finalize = (
        res.get("CHECKPOINT_ADVANCED") is True
        and ch.get("CAUSAL_HISTORY_PENDING") is True
        and pending.is_file()
        and worker_runs == 1
        and len(list((case / "results" / "causal" / "events").glob("*.json"))) == 0
    )
    recover = resolve_pending_history_only(case, "CASE-RH02")
    events_after = list((case / "results" / "causal" / "events").glob("*.json"))
    ok = (
        ok_after_finalize
        and recover.appended is True
        and len(events_after) == 1
        and not pending.is_file()
    )
    return ok, f"pending_cleared={not pending.is_file()} recover={recover.appended}", test_id, str(pending)


def rh_e2e_03_stagnant_pause() -> Result:
    test_id = "RH-E2E-03"
    case = _case("rh03")
    fid = "F-RH03"

    def setup(c: Path, _p: Dict[str, Any]) -> None:
        (c / "results" / f"GREEN-{fid}.md").write_text("g\n", encoding="utf-8")

    def always_fail(self, *args: Any, **kwargs: Any) -> Any:
        raise HistoryPause(HistoryErrorCode.AMBIGUOUS_TMP, "persistent fail", "retry")

    with patch.object(CausalEventStore, "publish_event", always_fail):
        res = _run_finalize(
            case,
            worker_id="GREEN_VERIFIER",
            case_id="CASE-RH03",
            finding_id=fid,
            orch="ORCH/rh03",
            checkpoint_generation=1,
            packet_generation=1,
            before_finalize=setup,
        )
    assert (res.get("CAUSAL_HISTORY") or {}).get("CAUSAL_HISTORY_PENDING") is True
    with patch.object(CausalEventStore, "publish_event", always_fail):
        r1 = resolve_pending_history_only(case, "CASE-RH03")
        r2 = resolve_pending_history_only(case, "CASE-RH03")
    ok = r1.history_pending and r2.pause_code == HistoryErrorCode.STAGNANT_CAUSAL_FAILURE.value
    return ok, f"r1={r1.pause_code} r2={r2.pause_code}", test_id, str(case / "results" / "causal" / "HISTORY_PENDING.json")


def rh_e2e_04_legacy_disabled() -> Result:
    test_id = "RH-E2E-04"
    case = _case("rh04", v3=False)
    fid = "F-RH04"

    def setup(c: Path, _p: Dict[str, Any]) -> None:
        (c / "results" / f"GREEN-{fid}.md").write_text("g\n", encoding="utf-8")

    res = _run_finalize(
        case,
        worker_id="GREEN_VERIFIER",
        case_id="CASE-RH04",
        finding_id=fid,
        orch="ORCH/rh04",
        checkpoint_generation=1,
        packet_generation=1,
        before_finalize=setup,
    )
    ch = res.get("CAUSAL_HISTORY") or {}
    ok = res.get("CHECKPOINT_ADVANCED") is True and ch.get("CAUSAL_SKIPPED") == "LIVE_V3_APPEND_DISABLED"
    return ok, str(ch), test_id, str(case)


def rh_e2e_05_multistep_semantic_narrative() -> Result:
    test_id = "RH-E2E-05"
    case = _case("rh05")
    case_id = "CASE-RH05"
    fid = "F-RH05"
    orch = "ORCH/rh05"
    prov = {
        "ORIGIN_CHANGE_ID": "chg-intro",
        "DISCOVERY_CHANGE_ID": "chg-active",
        "AFFECTED_CHANGE_IDS": ["chg-active"],
        "CURRENT_WORK_CHANGE_ID": "chg-active",
        "DEFECT_LOCUS": "app/service/handler.py",
    }

    def red_setup(c: Path, _p: Dict[str, Any]) -> None:
        red = c / "results" / f"RED-{fid}.md"
        red.write_text("symptom: null pointer on save\n", encoding="utf-8")

    r1 = _run_finalize(
        case,
        worker_id="RED_TEST_WORKER",
        case_id=case_id,
        finding_id=fid,
        orch=orch,
        checkpoint_generation=1,
        packet_generation=1,
        payload={
            "SYMPTOM_AND_REPRODUCTION": "save() raises TypeError on empty payload",
            "INITIAL_HYPOTHESIS": "validation layer too strict",
            "VIOLATED_INVARIANT": "handler must accept empty optional fields",
            "REJECTED_DIAGNOSES": [],
            "FAILED_FIXES": [{"attempt": 1, "summary": "loosen schema only", "outcome": "failed"}],
        },
        provenance=prov,
        before_finalize=red_setup,
    )
    cpg = int(r1.get("CHECKPOINT_GENERATION") or 2)
    pg = 2

    def classify_setup(c: Path, _p: Dict[str, Any]) -> None:
        trace = c / "results" / "ROUTE-TRACE.jsonl"
        trace.write_text("", encoding="utf-8")

    r2 = _run_finalize(
        case,
        worker_id="CLASSIFIER",
        case_id=case_id,
        finding_id=fid,
        orch=orch,
        checkpoint_generation=cpg,
        packet_generation=pg,
        payload={
            "REVISED_DIAGNOSIS": "normalization missing in planning path",
            "PERMITTING_PLANNING_DECISION": "allow empty optional in API contract",
            "FIX_ATTEMPT_OUTCOMES": [{"attempt": 2, "outcome": "partial"}],
            "REJECTED_DIAGNOSES": [{"id": "h1", "reason": "schema-only change insufficient"}],
            "ACTIVE_HYPOTHESES": [{"id": "h2", "text": "missing normalization"}],
        },
        provenance=prov,
        before_finalize=classify_setup,
    )
    cpg2 = int(r2.get("CHECKPOINT_GENERATION") or cpg + 1)
    pg2 = pg + 1

    def green_setup(c: Path, _p: Dict[str, Any]) -> None:
        (c / "results" / f"GREEN-{fid}.md").write_text("green: regression test pass\n", encoding="utf-8")

    r3 = _run_finalize(
        case,
        worker_id="GREEN_VERIFIER",
        case_id=case_id,
        finding_id=fid,
        orch=orch,
        checkpoint_generation=cpg2,
        packet_generation=pg2,
        payload={
            "ROOT_CAUSE": "normalization omitted in save path",
            "VERIFIED_RESOLUTION": "normalize before validate",
            "MISSED_VERIFICATION_GATE": "RED did not cover empty optional case",
            "EVIDENCE_REFS": [{"path": f"results/GREEN-{fid}.md", "role": "GREEN"}],
        },
        provenance=prov,
        before_finalize=green_setup,
    )
    store = CausalEventStore(case, case_id)
    replay = store.replay_deterministic()
    green_path = case / "results" / f"GREEN-{fid}.md"
    green_digest = _digest(green_path)
    bound = replay[-1]["EVIDENCE_BINDING"]["proof_digest"]
    narrative_ok = (
        replay[0].get("SYMPTOM_AND_REPRODUCTION")
        and replay[1].get("EVENT_KIND") == "CLASSIFICATION_RECORDED"
        and replay[1].get("REVISED_DIAGNOSIS")
        and replay[2].get("ROOT_CAUSE")
        and replay[2].get("VERIFIED_RESOLUTION")
    )
    ok = (
        all(x.get("CHECKPOINT_ADVANCED") for x in (r1, r2, r3))
        and len(replay) == 3
        and narrative_ok
        and bound == green_digest
    )
    return ok, f"events={len(replay)} narrative={narrative_ok}", test_id, str(case / "results" / "causal" / "events")


def rh_e2e_06_regression_via_finalize_live_hop() -> Result:
    """Real-hop GREEN finalize with confirmed regression finding + evidence binding."""
    test_id = "RH-E2E-06"
    case = _case("rh06")
    case_id = "CASE-RH06"
    fid = "F-RH06"
    orch = "ORCH/rh06"
    head = _git_head()
    reg_rel = f"results/REGRESSION-{fid}.md"

    def green_setup(c: Path, _p: Dict[str, Any]) -> None:
        (c / "results" / f"GREEN-{fid}.md").write_text("green ok\n", encoding="utf-8")
        reg = c / reg_rel
        reg.write_text("regression: legacy export endpoint 404\n", encoding="utf-8")

    def payload_with_digest(c: Path) -> Dict[str, Any]:
        reg_digest = _digest(c / reg_rel)
        return {
            "REGRESSION_FINDINGS": [
                {
                    "finding_id": "reg-export-404",
                    "summary": "legacy export endpoint returns 404 after handler refactor",
                    "evidence_ref": {"path": reg_rel, "digest": reg_digest},
                }
            ],
        }

    res = _run_finalize(
        case,
        worker_id="GREEN_VERIFIER",
        case_id=case_id,
        finding_id=fid,
        orch=orch,
        checkpoint_generation=1,
        packet_generation=1,
        payload={
            "REGRESSION_ASSESSMENT": "CONFIRMED_REGRESSION",
            "REPOSITORY_REVISION": head,
            "VERIFIED_RESOLUTION": "route restored with compatibility shim",
        },
        before_finalize=green_setup,
        payload_after_setup=payload_with_digest,
    )
    reg_digest = _digest(case / reg_rel)
    ch = res.get("CAUSAL_HISTORY") or {}
    store = CausalEventStore(case, case_id)
    replay = store.replay_deterministic()
    ev = replay[0] if replay else {}
    findings = ev.get("REGRESSION_FINDINGS") or []
    ok = (
        res.get("CHECKPOINT_ADVANCED") is True
        and ch.get("CAUSAL_APPENDED") is True
        and ev.get("REGRESSION_ASSESSMENT") == "CONFIRMED_REGRESSION"
        and len(findings) == 1
        and findings[0].get("evidence_ref", {}).get("digest") == reg_digest
        and (ev.get("REGRESSION_EXECUTION") or {}).get("repository_revision") == head
    )
    return ok, f"assessment={ev.get('REGRESSION_ASSESSMENT')} findings={len(findings)}", test_id, str(
        case / "results" / "causal" / "events"
    )


def _field_across_replay(replay: List[Dict[str, Any]], key: str) -> Any:
    """Первое содержательное значение поля по всем событиям (не merge с None)."""
    for event in replay:
        if key not in event:
            continue
        val = event[key]
        if val is None or val == [] or val == "":
            continue
        return val
    return None


def build_semantic_matrix(replay: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Матрица полноты по всему multi-step replay (RH-E2E-05)."""
    fields = [
        ("bug symptom and reproduction", "SYMPTOM_AND_REPRODUCTION"),
        ("defect locus", "DEFECT_LOCUS"),
        ("origin change id", "ORIGIN_CHANGE_ID"),
        ("discovery change id", "DISCOVERY_CHANGE_ID"),
        ("affected change ids", "AFFECTED_CHANGE_IDS"),
        ("current work change id", "CURRENT_WORK_CHANGE_ID"),
        ("violated invariant", "VIOLATED_INVARIANT"),
        ("initial hypothesis", "INITIAL_HYPOTHESIS"),
        ("rejected hypothesis", "REJECTED_DIAGNOSES"),
        ("revised diagnosis", "REVISED_DIAGNOSIS"),
        ("fix attempts outcomes", "FIX_ATTEMPT_OUTCOMES"),
        ("failed fixes", "FAILED_FIXES"),
        ("regression assessment", "REGRESSION_ASSESSMENT"),
        ("regression findings", "REGRESSION_FINDINGS"),
        ("root cause", "ROOT_CAUSE"),
        ("permitting planning decision", "PERMITTING_PLANNING_DECISION"),
        ("missed verification gate", "MISSED_VERIFICATION_GATE"),
        ("verified resolution", "VERIFIED_RESOLUTION"),
        ("evidence references", "EVIDENCE_REFS"),
    ]
    rows = []
    for label, key in fields:
        val = _field_across_replay(replay, key)
        if val is None or val == [] or val == "":
            status = "NOT_CAPTURED"
        elif val == "UNKNOWN" or (isinstance(val, str) and val.upper() == "UNKNOWN"):
            status = "EXPLICIT_UNKNOWN"
        else:
            status = "CAPTURED_WITH_EVIDENCE"
        rows.append({"field": label, "key": key, "status": status})
    return rows


def main() -> int:
    head = _git_head()
    exec_id = f"real-hop-e2e@{head[:12]}"
    tests = [
        rh_e2e_01_green_finalize_causal,
        rh_e2e_02_append_fail_history_retry,
        rh_e2e_03_stagnant_pause,
        rh_e2e_04_legacy_disabled,
        rh_e2e_05_multistep_semantic_narrative,
        rh_e2e_06_regression_via_finalize_live_hop,
    ]
    rows = []
    failed = 0
    for fn in tests:
        try:
            ok, detail, tid, artifact = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail, tid, artifact = False, traceback.format_exc(), fn.__name__, ""
        if not ok:
            failed += 1
        rows.append(
            {
                "test_id": tid,
                "passed": ok,
                "detail": detail,
                "artifact_path": artifact,
                "command": "python3 harness/run_real_hop_causal_history_e2e.py",
                "execution_id": exec_id,
                "revision": head,
            }
        )
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail}")

    # semantic matrix from rh05 if passed
    matrix: List[Dict[str, str]] = []
    rh06_case = HARNESS / "rh06"
    if rh06_case.is_dir():
        try:
            replay = CausalEventStore(rh06_case, "CASE-RH06").replay_deterministic()
            if replay:
                matrix = build_semantic_matrix(replay)
        except Exception:
            pass

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "REAL_HOP_E2E_VERIFIED" if failed == 0 else "FAILED",
        "note": "Full closure also requires base corrective CT-18 attestation (see internal opsx closure report).",
        "execution_id": exec_id,
        "revision": head,
        "real_finalize_live_hop_tests": rows,
        "semantic_history_matrix": matrix,
        "live_activation_enabled": False,
    }
    (ARTIFACTS / "real_hop_e2e_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
