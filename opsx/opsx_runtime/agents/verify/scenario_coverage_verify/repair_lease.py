"""Reconcile orphan ``repair: started`` journal rows against run_dialogs evidence.

Если verify оборван после ``mark_repair_started``, journal не должен блокировать
все следующие прогоны. Активный adapter repair (``sdk_status: running``)
по-прежнему даёт PAUSE без повторного repair.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

_REPAIR_AGENT_FOLDER = "scenario_test_repair"
_RUNNING_STATUSES = frozenset({"running", "active", "in_progress"})


class OpenRepairDecision(str, Enum):
    """Исход проверки незакрытого repair lease в journal."""

    CONTINUE = "CONTINUE"
    PAUSE_STILL_ACTIVE = "PAUSE_STILL_ACTIVE"
    PAUSE_UNRESOLVED = "PAUSE_UNRESOLVED"


@dataclass(frozen=True, slots=True)
class OpenRepairResolution:
    """Решение reconcile и опциональная причина PAUSE."""

    decision: OpenRepairDecision
    pause_reason: str = ""
    repair_run_uuid: str = ""


def repair_dialog_root(repo: Path, change_id: str, repair_run_uuid: str) -> Path:
    """Каталог scenario_test_repair для verify-run."""
    return (
        resource_path(repo, "opsx", "runs", "dialogs", change_id, repair_run_uuid.strip(), _REPAIR_AGENT_FOLDER)
    )


def _read_sdk_status(meta_path: Path) -> str:
    if not meta_path.is_file():
        return ""
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("sdk_status") or "").strip().lower()


def _is_running_status(status: str) -> bool:
    if not status:
        return False
    if status in _RUNNING_STATUSES:
        return True
    return status not in ("finished", "failed", "cancelled", "canceled", "stopped")


def _latest_meta_path(dialog_root: Path) -> Path | None:
    """Самый свежий meta: revisions/run-*/meta.json, иначе корневой meta.json."""
    revisions = dialog_root / "revisions"
    revision_candidates: list[Path] = []
    if revisions.is_dir():
        revision_candidates = [
            meta for meta in revisions.glob("run-*/meta.json") if meta.is_file()
        ]
    if revision_candidates:
        return max(revision_candidates, key=lambda p: p.stat().st_mtime)
    root_meta = dialog_root / "meta.json"
    if root_meta.is_file():
        return root_meta
    return None


def latest_repair_sdk_status(dialog_root: Path) -> str:
    """``running`` | ``finished`` | ``missing`` (нет каталога/meta)."""
    if not dialog_root.is_dir():
        return "missing"
    meta_path = _latest_meta_path(dialog_root)
    if meta_path is None:
        return "missing"
    status = _read_sdk_status(meta_path)
    if not status:
        return "missing"
    if _is_running_status(status):
        return "running"
    return "finished"


def any_repair_still_running(repo: Path, change_id: str) -> bool:
    """Есть ли незавершённый scenario_test_repair по любому run этого change."""
    base = resource_path(repo, "opsx", "runs", "dialogs", change_id)
    if not base.is_dir():
        return False
    for run_dir in base.iterdir():
        if not run_dir.is_dir():
            continue
        if latest_repair_sdk_status(run_dir / _REPAIR_AGENT_FOLDER) == "running":
            return True
    return False


def resolve_open_repair(
    row: dict[str, Any],
    *,
    repo: Path,
    change_id: str,
    current_run_uuid: str,
) -> OpenRepairResolution:
    """Проверить journal-строку с ``repair == started`` перед row-walk."""
    repair = str(row.get("repair") or "")
    if repair != "started":
        return OpenRepairResolution(OpenRepairDecision.CONTINUE)

    lease_uuid = str(row.get("repair_run_uuid") or "").strip()
    if lease_uuid:
        status = latest_repair_sdk_status(repair_dialog_root(repo, change_id, lease_uuid))
        if status == "running":
            reason = f"REPAIR_STILL_ACTIVE: {lease_uuid}"
            _LOGGER.info(
                "repair_lease pause still_active change_id=%s repair_run_uuid=%s",
                change_id,
                lease_uuid,
            )
            return OpenRepairResolution(
                OpenRepairDecision.PAUSE_STILL_ACTIVE,
                pause_reason=reason,
                repair_run_uuid=lease_uuid,
            )
        if status == "missing" and lease_uuid == current_run_uuid.strip():
            return OpenRepairResolution(
                OpenRepairDecision.PAUSE_UNRESOLVED,
                pause_reason=f"REPAIR_OUTCOME_UNKNOWN: in-flight repair in run {lease_uuid}",
                repair_run_uuid=lease_uuid,
            )
        return OpenRepairResolution(
            OpenRepairDecision.CONTINUE,
            repair_run_uuid=lease_uuid,
        )

    if any_repair_still_running(repo, change_id):
        return OpenRepairResolution(
            OpenRepairDecision.PAUSE_STILL_ACTIVE,
            pause_reason="REPAIR_STILL_ACTIVE: legacy started row; repair SDK running",
        )

    _LOGGER.info(
        "repair_lease orphan_abort legacy started change_id=%s current_run=%s",
        change_id,
        current_run_uuid,
    )
    return OpenRepairResolution(OpenRepairDecision.CONTINUE)
