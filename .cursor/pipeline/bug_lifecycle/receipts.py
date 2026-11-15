"""Machine-readable mission transition receipts for orch-bug-fix."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "opsx-orch-mission-receipt-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_sha256(payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "content_sha256"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_receipt(
    repo_root: Path,
    *,
    mission_id: str,
    stage: str,
    execution_id: str,
    head: str,
    writer: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    receipt_dir = (
        repo_root / "opsx" / "runs" / "orch-missions" / mission_id / "receipts"
    )
    receipt_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "stage": stage,
        "mission_id": mission_id,
        "execution_id": execution_id,
        "head": head,
        "writer": writer,
        "written_at": utc_now(),
        **body,
    }
    payload["content_sha256"] = canonical_sha256(payload)
    name = f"{stage.replace('/', '_')}-{payload['content_sha256'][:16]}.json"
    path = receipt_dir / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return payload
