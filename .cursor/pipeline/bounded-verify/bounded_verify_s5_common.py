# -*- coding: utf-8 -*-
"""Shared S5-A helpers: hermetic gate, producer identity, policy binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]


def s5_hermetic_adapters_enabled(*, root: Path) -> bool:
    """Delegated to explicit workspace registration (see bounded_verify_s5_hermetic_runtime)."""
    from bounded_verify_s5_hermetic_runtime import s5_hermetic_adapters_enabled as _enabled

    return _enabled(root=root)


def canonical_evidence_producer(obligation_id: str) -> str:
    """Canonical writer registration identity for receipt validation."""
    return f"bounded-verify:{obligation_id}"


def policy_identity_for_change(change_id: str, *, root: Path) -> dict[str, str]:
    """Minimal policy identity bound at freeze (fixture-safe)."""
    from bounded_verify_plan_profile import policy_revision_bindings

    bindings = policy_revision_bindings(root=root)
    return {
        "change_id": change_id,
        "verification_obligations_revision": bindings.get(
            ".cursor/pipeline/bounded-verify/verification_obligations.py",
            "",
        ),
        "verify_policy_revision": bindings.get("openspec/policies/VERIFY_POLICY.md", ""),
    }


def input_digest_for_change(change_id: str, *, root: Path) -> str:
    """Role digest snapshot identity for receipt binding."""
    from bounded_verify_snapshot import live_implementation_snapshot

    plan_path = root / "openspec/changes" / change_id / "bounded-verify/plan.json"
    schema = "bounded-verify-plan-v1"
    if plan_path.is_file():
        try:
            doc = json.loads(plan_path.read_text(encoding="utf-8"))
            schema = str(doc.get("schema_version", schema))
        except (OSError, json.JSONDecodeError):
            pass
    live = live_implementation_snapshot(change_id, root=root, plan_schema=schema)
    payload = json.dumps(live, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_frozen_plan_ids(change_id: str, *, root: Path) -> tuple[str, str]:
    """plan_id and plan_fingerprint from frozen carrier when present."""
    plan_path = root / "openspec/changes" / change_id / "bounded-verify/plan.json"
    if not plan_path.is_file():
        return ("", "")
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    return (str(doc.get("plan_id", "")), str(doc.get("plan_fingerprint", "")))


def require_frozen_plan_binding(change_id: str, *, root: Path) -> tuple[str, str]:
    """plan_id + 64-char fingerprint required for fixture receipt publication."""
    plan_path = root / "openspec/changes" / change_id / "bounded-verify/plan.json"
    if not plan_path.is_file():
        raise ValueError("FROZEN_PLAN_BINDING_REQUIRED")
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    if str(doc.get("plan_status", "")) != "FROZEN":
        raise ValueError("FROZEN_PLAN_BINDING_REQUIRED")
    plan_id = str(doc.get("plan_id", "")).strip()
    fp = str(doc.get("plan_fingerprint", "")).strip()
    if not plan_id or len(fp) != 64:
        raise ValueError("FROZEN_PLAN_BINDING_REQUIRED")
    return plan_id, fp
