# -*- coding: utf-8 -*-
"""CAUSAL_HISTORY_APPENDER — единственный writer canonical causal events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import CausalHistorySettings, load_settings
from .errors import HistoryPause
from .event_builder import (
    build_event_body,
    build_evidence_binding,
    operation_id_for_hop,
)
from .retry import HistoryRetryOrchestrator
from .store import CausalEventStore, PublishResult


@dataclass
class AppendHopResult:
    appended: bool
    skipped_reason: Optional[str]
    operation_id: Optional[str]
    event_id: Optional[str]
    history_pending: bool
    pause_code: Optional[str]
    detail: Optional[str]


class CausalHistoryAppender:
    """Оркестраторский appender (не worker hop)."""

    def __init__(self, case_root: Path, case_id: str) -> None:
        self.case_root = Path(case_root)
        self.case_id = case_id
        self.store = CausalEventStore(self.case_root, case_id)

    def append_request_path(self, worker_execution_id: str) -> Path:
        slug = worker_execution_id.replace("/", "_")
        return self.case_root / "team" / "packets" / f"{slug}.causal_append_request.json"

    def write_append_request(self, request: Dict[str, Any], worker_execution_id: str) -> Path:
        path = self.append_request_path(worker_execution_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def publish_from_request(self, request: Dict[str, Any]) -> PublishResult:
        from .store import EvidenceBinding

        ev = EvidenceBinding(
            proof_path=request["EVIDENCE_BINDING"]["proof_path"],
            proof_digest=request["EVIDENCE_BINDING"]["proof_digest"],
            execution_revision=request["EVIDENCE_BINDING"]["execution_revision"],
        )
        return self.store.publish_event(
            request["OPERATION_ID"],
            request["EVENT_BODY"],
            ev,
            event_slot=str(request.get("EVENT_SLOT", "CAUSAL_EVENT")),
        )

    def history_only_retry(self, request: Dict[str, Any]) -> PublishResult:
        """Повтор append без повторного semantic worker."""
        return self.publish_from_request(request)


def _pending_path(case_root: Path) -> Path:
    return Path(case_root) / "results" / "causal" / "HISTORY_PENDING.json"


def _write_pending(case_root: Path, payload: Dict[str, Any]) -> None:
    path = _pending_path(case_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_from_hop_finalize(
    *,
    case_root: Path,
    case_id: str,
    worker_id: str,
    worker_execution_id: str,
    finding_id: Optional[str],
    intent: Dict[str, Any],
    out_pkt: Dict[str, Any],
    artifact_paths: List[str],
    provenance: Optional[Dict[str, Any]] = None,
) -> AppendHopResult:
    """
    Записывает causal event после успешного worker finalize.

    Не откатывает hop при ошибке append; выставляет HISTORY_PENDING.
    """
    settings = load_settings(case_root)
    if not settings.live_v3_append_enabled:
        return AppendHopResult(
            appended=False,
            skipped_reason="LIVE_V3_APPEND_DISABLED",
            operation_id=None,
            event_id=None,
            history_pending=False,
            pause_code=None,
            detail=None,
        )
    if settings.history_contract_version in ("legacy", "EXEMPT"):
        return AppendHopResult(
            appended=False,
            skipped_reason=f"CONTRACT_{settings.history_contract_version}",
            operation_id=None,
            event_id=None,
            history_pending=False,
            pause_code=None,
            detail=None,
        )

    appender = CausalHistoryAppender(case_root, case_id)
    op_id = operation_id_for_hop(worker_execution_id)
    exec_rev = str(intent.get("CHECKPOINT_GENERATION", "0")) + ":" + worker_execution_id
    try:
        binding = build_evidence_binding(
            case_root, artifact_paths, worker_id, finding_id, exec_rev
        )
    except FileNotFoundError as exc:
        _write_pending(
            case_root,
            {
                "OPERATION_ID": op_id,
                "REASON": "EVIDENCE_MISSING",
                "DETAIL": str(exc),
            },
        )
        return AppendHopResult(
            appended=False,
            skipped_reason=None,
            operation_id=op_id,
            event_id=None,
            history_pending=True,
            pause_code="EVIDENCE_MISSING",
            detail=str(exc),
        )

    event_body = build_event_body(
        settings_version=settings.history_contract_version,
        worker_id=worker_id,
        finding_id=finding_id,
        intent=intent,
        out_pkt=out_pkt,
        provenance=provenance,
    )
    request = {
        "OPERATION_ID": op_id,
        "EVENT_SLOT": "CAUSAL_EVENT",
        "EVENT_BODY": event_body,
        "EVIDENCE_BINDING": {
            "proof_path": binding.proof_path,
            "proof_digest": binding.proof_digest,
            "execution_revision": binding.execution_revision,
        },
    }
    appender.write_append_request(request, worker_execution_id)

    try:
        result = appender.store.publish_event(op_id, event_body, binding)
        pending = _pending_path(case_root)
        if pending.is_file():
            pending.unlink()
        return AppendHopResult(
            appended=True,
            skipped_reason=None,
            operation_id=op_id,
            event_id=result.event_id,
            history_pending=False,
            pause_code=None,
            detail=None,
        )
    except HistoryPause as pause:
        _write_pending(
            case_root,
            {
                "OPERATION_ID": op_id,
                "REASON": pause.code.value,
                "DETAIL": pause.detail,
                "RECOVERY": pause.recovery,
                "REQUEST_PATH": str(appender.append_request_path(worker_execution_id)),
            },
        )
        return AppendHopResult(
            appended=False,
            skipped_reason=None,
            operation_id=op_id,
            event_id=None,
            history_pending=True,
            pause_code=pause.code.value,
            detail=pause.detail,
        )


def resolve_pending_history_only(case_root: Path, case_id: str) -> AppendHopResult:
    """
    History-only recovery из HISTORY_PENDING + сохранённого append request.

    Не запускает semantic worker.
    """
    pending_path = _pending_path(case_root)
    if not pending_path.is_file():
        return AppendHopResult(
            appended=False,
            skipped_reason="NO_PENDING",
            operation_id=None,
            event_id=None,
            history_pending=False,
            pause_code=None,
            detail=None,
        )
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    req_path = Path(pending.get("REQUEST_PATH", ""))
    if not req_path.is_file():
        return AppendHopResult(
            appended=False,
            skipped_reason=None,
            operation_id=pending.get("OPERATION_ID"),
            event_id=None,
            history_pending=True,
            pause_code="REQUEST_MISSING",
            detail="causal_append_request missing",
        )
    request = json.loads(req_path.read_text(encoding="utf-8"))
    appender = CausalHistoryAppender(case_root, case_id)
    retry = HistoryRetryOrchestrator(appender.store)
    op_id = str(request["OPERATION_ID"])
    try:
        result = retry.run_history_only(op_id, lambda: appender.publish_from_request(request))
        if pending_path.is_file():
            pending_path.unlink()
        return AppendHopResult(
            appended=True,
            skipped_reason=None,
            operation_id=op_id,
            event_id=result.event_id,
            history_pending=False,
            pause_code=None,
            detail=None,
        )
    except HistoryPause as pause:
        _write_pending(
            case_root,
            {
                **pending,
                "REASON": pause.code.value,
                "DETAIL": pause.detail,
            },
        )
        return AppendHopResult(
            appended=False,
            skipped_reason=None,
            operation_id=op_id,
            event_id=None,
            history_pending=True,
            pause_code=pause.code.value,
            detail=pause.detail,
        )
