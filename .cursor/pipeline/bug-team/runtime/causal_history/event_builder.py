# -*- coding: utf-8 -*-
"""Построение V3 causal event body из finalize hop контекста."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from .regression_capture import build_regression_capture
from .store import EvidenceBinding

WORKER_EVENT_KIND: Dict[str, str] = {
    "MATERIALIZER": "DISCOVERY_RECORDED",
    "CLASSIFIER": "CLASSIFICATION_RECORDED",
    "RED_TEST_WORKER": "RED_ATTEMPT_RECORDED",
    "IMPLEMENTATION_EXECUTOR": "FIX_ATTEMPT_RECORDED",
    "GREEN_VERIFIER": "GREEN_CONFIRMED",
    "FAMILY_LEARNING_WORKER": "LEARNING_DECISION_RECORDED",
    "CLOSURE_OPERATOR": "RESOLUTION_RECORDED",
}


def operation_id_for_hop(worker_execution_id: str, event_slot: str = "CAUSAL_EVENT") -> str:
    return f"{worker_execution_id}:{event_slot}"


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pick_primary_proof(
    case_root: Path,
    artifact_paths: List[str],
    worker_id: str,
    finding_id: Optional[str],
) -> Path:
    """Выбирает основной proof artifact для evidence binding."""
    root = Path(case_root)
    candidates: List[Path] = []
    fid = finding_id or ""
    patterns = {
        "RED_TEST_WORKER": [f"results/RED-{fid}.md", "results/RED-"],
        "GREEN_VERIFIER": [f"results/GREEN-{fid}.md", "results/GREEN-"],
        "CLASSIFIER": ["results/ROUTE-TRACE.jsonl"],
    }
    for rel in artifact_paths:
        p = root / rel
        if p.is_file():
            candidates.append(p)
    for pat in patterns.get(worker_id, []):
        if pat.endswith("-") and fid:
            for p in (root / "results").glob(f"{pat.split('/')[-1]}*"):
                if p.is_file():
                    candidates.append(p)
        else:
            p = root / pat
            if p.is_file():
                candidates.append(p)
    out_pkt_glob = list((root / "team" / "packets").glob("*.out.json"))
    if out_pkt_glob:
        candidates.append(sorted(out_pkt_glob)[-1])
    if not candidates:
        ledger = root / "LEDGER.md"
        if ledger.is_file():
            candidates.append(ledger)
    if not candidates:
        raise FileNotFoundError("no proof artifact for causal binding")
    return candidates[0]


def build_evidence_binding(
    case_root: Path,
    artifact_paths: List[str],
    worker_id: str,
    finding_id: Optional[str],
    execution_revision: str,
) -> EvidenceBinding:
    proof = pick_primary_proof(case_root, artifact_paths, worker_id, finding_id)
    rel = str(proof.relative_to(case_root)).replace("\\", "/")
    return EvidenceBinding(
        proof_path=rel,
        proof_digest=_file_digest(proof),
        execution_revision=execution_revision,
    )


def build_event_body(
    *,
    settings_version: str,
    worker_id: str,
    finding_id: Optional[str],
    intent: Dict[str, Any],
    out_pkt: Dict[str, Any],
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Минимальный V3 capture: discovery/classification/provenance + hop diagnostics.

    Поля гипотез/отвергнутых диагнозов и regression capture передаются через
    out_pkt PAYLOAD при наличии (см. regression_capture).
    """
    kind = WORKER_EVENT_KIND.get(worker_id, "HOP_COMPLETED")
    prov = provenance or {}
    payload = out_pkt.get("PAYLOAD") if isinstance(out_pkt.get("PAYLOAD"), dict) else {}
    exec_rev = str(intent.get("CHECKPOINT_GENERATION", "0")) + ":" + (
        intent.get("WORKER_EXECUTION_ID") or intent.get("ACTIVE_WORKER_EXECUTION_ID") or ""
    )
    regression = build_regression_capture(
        out_pkt,
        execution_revision=exec_rev,
        repository_revision=payload.get("REPOSITORY_REVISION") or intent.get("REPOSITORY_REVISION"),
    )
    body: Dict[str, Any] = {
        "SCHEMA_VERSION": "bug_history_v3_event/1",
        "HISTORY_CONTRACT_VERSION": settings_version,
        "EVENT_KIND": kind,
        "WORKER_ID": worker_id,
        "FINDING_ID": finding_id,
        "WORKER_EXECUTION_ID": intent.get("WORKER_EXECUTION_ID") or intent.get("ACTIVE_WORKER_EXECUTION_ID"),
        "ORCHESTRATION_ID": intent.get("ORCHESTRATION_ID"),
        "TERMINAL_STATUS": out_pkt.get("TERMINAL_STATUS"),
        "HOP_STATUS": out_pkt.get("STATUS"),
        "ORIGIN_CHANGE_ID": prov.get("ORIGIN_CHANGE_ID"),
        "DISCOVERY_CHANGE_ID": prov.get("DISCOVERY_CHANGE_ID"),
        "AFFECTED_CHANGE_IDS": prov.get("AFFECTED_CHANGE_IDS", []),
        "CURRENT_WORK_CHANGE_ID": prov.get("CURRENT_WORK_CHANGE_ID"),
        "DEFECT_LOCUS": payload.get("DEFECT_LOCUS") or out_pkt.get("DEFECT_LOCUS") or prov.get("DEFECT_LOCUS"),
        "ACTIVE_HYPOTHESES": payload.get("ACTIVE_HYPOTHESES", out_pkt.get("ACTIVE_HYPOTHESES", [])),
        "REJECTED_DIAGNOSES": payload.get("REJECTED_DIAGNOSES", out_pkt.get("REJECTED_DIAGNOSES", [])),
        "FAILED_FIXES": payload.get("FAILED_FIXES", out_pkt.get("FAILED_FIXES", [])),
        **regression,
        "RETROSPECTIVE_SUFFICIENCY": out_pkt.get("RETROSPECTIVE_SUFFICIENCY", "UNKNOWN"),
        "SYMPTOM_AND_REPRODUCTION": payload.get("SYMPTOM_AND_REPRODUCTION")
        or out_pkt.get("SYMPTOM_AND_REPRODUCTION"),
        "VIOLATED_INVARIANT": payload.get("VIOLATED_INVARIANT") or out_pkt.get("VIOLATED_INVARIANT"),
        "INITIAL_HYPOTHESIS": payload.get("INITIAL_HYPOTHESIS") or out_pkt.get("INITIAL_HYPOTHESIS"),
        "REVISED_DIAGNOSIS": payload.get("REVISED_DIAGNOSIS") or out_pkt.get("REVISED_DIAGNOSIS"),
        "ROOT_CAUSE": payload.get("ROOT_CAUSE") or out_pkt.get("ROOT_CAUSE"),
        "PERMITTING_PLANNING_DECISION": payload.get("PERMITTING_PLANNING_DECISION")
        or out_pkt.get("PERMITTING_PLANNING_DECISION"),
        "MISSED_VERIFICATION_GATE": payload.get("MISSED_VERIFICATION_GATE")
        or out_pkt.get("MISSED_VERIFICATION_GATE"),
        "VERIFIED_RESOLUTION": payload.get("VERIFIED_RESOLUTION") or out_pkt.get("VERIFIED_RESOLUTION"),
        "EVIDENCE_REFS": payload.get("EVIDENCE_REFS", out_pkt.get("EVIDENCE_REFS", [])),
        "FIX_ATTEMPT_OUTCOMES": payload.get("FIX_ATTEMPT_OUTCOMES", out_pkt.get("FIX_ATTEMPT_OUTCOMES", [])),
    }
    return body
