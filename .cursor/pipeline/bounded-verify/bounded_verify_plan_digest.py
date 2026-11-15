# -*- coding: utf-8 -*-
"""Plan snapshot digest helpers for assignment/result binding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bounded_verify_snapshot import PLAN_SCHEMA_V2, live_implementation_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]


def frozen_content_digest(plan: Mapping[str, Any]) -> str:
    snap = plan.get("implementation_snapshot")
    if not isinstance(snap, dict):
        return ""
    return str(snap.get("content_digest", ""))


def content_digest_required_for_drain(plan: Mapping[str, Any]) -> str | None:
    """v2 plans require content_digest at drain; v1 explicit None (compat)."""
    if str(plan.get("schema_version", "")) == PLAN_SCHEMA_V2:
        digest = frozen_content_digest(plan)
        return digest if len(digest) == 64 else ""
    return None


def live_content_digest(change_id: str, plan: Mapping[str, Any], *, root) -> str:
    schema = str(plan.get("schema_version", "bounded-verify-plan-v1"))
    live = live_implementation_snapshot(change_id, root=root, plan_schema=schema)
    return str(live.get("content_digest", ""))
