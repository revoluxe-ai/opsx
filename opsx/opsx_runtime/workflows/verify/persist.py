"""Запись артефактов PHASE_IMPL verify под change и run dir."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from workflows.artifact_paths import impl_verify_receipt_path


def persist_impl_verify_artifacts(
    outcome: Mapping[str, Any],
    *,
    change_id: str,
    repo_root: Path,
    stream_log: Path,
) -> dict[str, str]:
    """Сохранить только ``IMPL-VERIFY-RECEIPT.json``; вернуть путь receipt."""
    receipt_path = impl_verify_receipt_path(repo_root, change_id)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    preflight = outcome.get("deterministic_preflight")
    if isinstance(preflight, dict):
        preflight = dict(preflight)
    receipt = {
        "schema_version": 1,
        "mode": "impl",
        "change_id": change_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "marker": outcome.get("marker"),
        "verdict": outcome.get("verdict"),
        "run_id": outcome.get("run_id"),
        "agent_id": outcome.get("agent_id"),
        "stream_log": str(stream_log),
        "usage": outcome.get("usage"),
        "cost": outcome.get("cost"),
        "cost_status": outcome.get("cost_status"),
        "billing_error": outcome.get("billing_error"),
        "usage_source": outcome.get("usage_source"),
        "deterministic_preflight": preflight,
        "answer": outcome.get("answer"),
    }
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "receipt_path": str(receipt_path),
    }
