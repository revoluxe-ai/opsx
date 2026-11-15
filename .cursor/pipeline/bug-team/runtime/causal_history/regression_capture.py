# -*- coding: utf-8 -*-
"""Нормализация regression findings для Bug History V3 causal events."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

REGRESSION_ASSESSMENT_CONFIRMED = "CONFIRMED_REGRESSION"
REGRESSION_ASSESSMENT_ABSENT = "NO_REGRESSION_CONFIRMED"
REGRESSION_ASSESSMENT_NOT_ASSESSED = "NOT_ASSESSED"
REGRESSION_ASSESSMENT_MISSING = "MISSING_REQUIRED_DATA"

_ALLOWED_ASSESSMENTS = frozenset(
    {
        REGRESSION_ASSESSMENT_CONFIRMED,
        REGRESSION_ASSESSMENT_ABSENT,
        REGRESSION_ASSESSMENT_NOT_ASSESSED,
        REGRESSION_ASSESSMENT_MISSING,
    }
)


def _payload_dict(out_pkt: Dict[str, Any]) -> Dict[str, Any]:
    raw = out_pkt.get("PAYLOAD")
    return raw if isinstance(raw, dict) else {}


def _normalize_finding_item(item: Any, index: int) -> Optional[Dict[str, Any]]:
    """Приводит один finding к канонической записи с evidence_ref."""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {
            "finding_id": f"reg-{index + 1}",
            "summary": text,
            "evidence_ref": None,
        }
    if not isinstance(item, dict):
        return None
    summary = str(item.get("summary") or item.get("text") or item.get("flag") or "").strip()
    if not summary:
        return None
    evidence_ref = item.get("evidence_ref")
    if evidence_ref is not None and not isinstance(evidence_ref, dict):
        evidence_ref = {"path": str(evidence_ref)}
    finding: Dict[str, Any] = {
        "finding_id": str(item.get("finding_id") or item.get("id") or f"reg-{index + 1}"),
        "summary": summary,
        "evidence_ref": evidence_ref,
    }
    if item.get("severity"):
        finding["severity"] = item["severity"]
    return finding


def _findings_from_flags(flags: Any) -> List[Dict[str, Any]]:
    if not isinstance(flags, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(flags):
        norm = _normalize_finding_item(item, i)
        if norm:
            out.append(norm)
    return out


def build_regression_capture(
    out_pkt: Dict[str, Any],
    *,
    execution_revision: str,
    repository_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Строит поля regression для causal event body.

    Не подставляет placeholder PASS и не трактует пустой REGRESSION_FLAGS как
    подтверждённое отсутствие регрессии.

    Args:
        out_pkt: WORKER_OUTPUT packet (finalize).
        execution_revision: execution_revision из evidence binding / intent.
        repository_revision: опциональный VCS revision из worker PAYLOAD.

    Returns:
        Словарь с REGRESSION_ASSESSMENT, REGRESSION_FINDINGS, REGRESSION_ABSENCE_EVIDENCE,
        REGRESSION_EXECUTION (identity), устаревший REGRESSION_FLAGS только при confirmed.
    """
    payload = _payload_dict(out_pkt)
    top = out_pkt

    def _get(key: str) -> Any:
        if key in payload:
            return payload[key]
        return top.get(key)

    assessment_raw = _get("REGRESSION_ASSESSMENT")
    assessment: Optional[str] = None
    if isinstance(assessment_raw, str) and assessment_raw in _ALLOWED_ASSESSMENTS:
        assessment = assessment_raw

    findings_raw = _get("REGRESSION_FINDINGS")
    findings: List[Dict[str, Any]] = []
    if isinstance(findings_raw, list):
        for i, item in enumerate(findings_raw):
            norm = _normalize_finding_item(item, i)
            if norm:
                findings.append(norm)

    if not findings:
        flags = _get("REGRESSION_FLAGS")
        findings = _findings_from_flags(flags)

    absence = _get("REGRESSION_ABSENCE_EVIDENCE")
    if absence is not None and not isinstance(absence, dict):
        absence = {"path": str(absence)}

    if assessment is None:
        if findings:
            assessment = REGRESSION_ASSESSMENT_CONFIRMED
        elif absence:
            assessment = REGRESSION_ASSESSMENT_ABSENT
        elif assessment_raw == REGRESSION_ASSESSMENT_MISSING or _get("REGRESSION_DATA_INCOMPLETE"):
            assessment = REGRESSION_ASSESSMENT_MISSING
        else:
            assessment = REGRESSION_ASSESSMENT_NOT_ASSESSED

    if assessment == REGRESSION_ASSESSMENT_CONFIRMED and not findings:
        assessment = REGRESSION_ASSESSMENT_MISSING

    if assessment == REGRESSION_ASSESSMENT_ABSENT and not absence:
        assessment = REGRESSION_ASSESSMENT_MISSING

    repo_rev = _get("REPOSITORY_REVISION") or repository_revision

    body: Dict[str, Any] = {
        "REGRESSION_ASSESSMENT": assessment,
        "REGRESSION_FINDINGS": findings if assessment == REGRESSION_ASSESSMENT_CONFIRMED else [],
        "REGRESSION_ABSENCE_EVIDENCE": absence if assessment == REGRESSION_ASSESSMENT_ABSENT else None,
        "REGRESSION_EXECUTION": {
            "execution_revision": execution_revision,
            "repository_revision": repo_rev,
            "worker_execution_id": top.get("WORKER_EXECUTION_ID") or payload.get("WORKER_EXECUTION_ID"),
        },
    }
    if assessment == REGRESSION_ASSESSMENT_CONFIRMED and findings:
        body["REGRESSION_FLAGS"] = [f["summary"] for f in findings]
    else:
        body["REGRESSION_FLAGS"] = None
    return body
