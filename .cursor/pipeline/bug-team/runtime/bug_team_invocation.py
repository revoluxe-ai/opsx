# -*- coding: utf-8 -*-
"""Разбор вызовов /opsx-bug-team (production routing layer).

Не является simulated Task backend; используется live command routing и harness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _is_placeholder_case_id(case_id: Optional[str]) -> bool:
    """True для шаблонных case= (не CASE_RESUME)."""
    if case_id is None:
        return True
    cid = case_id.strip().strip('"').rstrip(".,;")
    if not cid:
        return True
    if cid.upper() in {"CASE_ID", "<CASE_ID>"}:
        return True
    if cid.startswith("<") and cid.endswith(">"):
        return True
    return False


def parse_bug_team_invocation(args: str, context_text: str = "") -> Dict[str, Any]:
    """Parse /opsx-bug-team invocation modes (orchestrator-only)."""
    text = (args or "").strip()
    case_id = None
    signal = None
    if "case=" in text:
        for tok in text.split():
            if tok.startswith("case="):
                case_id = tok.split("=", 1)[1].strip().strip('"')
        if _is_placeholder_case_id(case_id):
            case_id = None
    if "signal=" in text:
        start = text.find("signal=")
        rest = text[start + 7 :]
        if rest.startswith('"'):
            end = rest.find('"', 1)
            signal = rest[1:end] if end > 0 else rest.strip('"')
        else:
            signal = rest.strip()
    remainder_parts: List[str] = []
    for line in text.splitlines() or [text]:
        tokens = line.split()
        kept: List[str] = []
        for tok in tokens:
            if tok.startswith("case=") or tok.startswith("signal="):
                continue
            kept.append(tok)
        if kept:
            remainder_parts.append(" ".join(kept))
    remainder = "\n".join(remainder_parts).strip()
    if signal and remainder == signal:
        remainder = ""

    if case_id and remainder:
        return {
            "MODE": "BLOCKED_AMBIGUOUS_INVOCATION",
            "CASE_ID": case_id,
            "RAW": remainder,
        }
    if case_id and signal:
        return {
            "MODE": "BLOCKED_AMBIGUOUS_INVOCATION",
            "CASE_ID": case_id,
            "RAW": signal,
        }
    if case_id:
        return {"MODE": "CASE_RESUME", "CASE_ID": case_id}
    if signal:
        return {
            "MODE": "EXPLICIT_SIGNAL",
            "RAW_INTAKE_TEXT": signal,
            "RAW_INTAKE_SOURCE": "USER_MESSAGE",
        }
    if text:
        return {
            "MODE": "FREE_FORM",
            "RAW_INTAKE_TEXT": text,
            "RAW_INTAKE_SOURCE": "USER_MESSAGE",
        }
    if context_text.strip():
        return {
            "MODE": "CURRENT_CONTEXT",
            "RAW_INTAKE_TEXT": context_text.strip(),
            "RAW_INTAKE_SOURCE": "CURRENT_CHAT_CONTEXT",
        }
    return {"MODE": "BLOCKED_INTAKE_REQUIRED"}
