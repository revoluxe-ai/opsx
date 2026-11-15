"""S9 debt board verification (trusted source required; no silent PASS)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

OPEN_DEBT_PATH = Path("opsx/runs/debt-board/open-items.json")
TRUSTED_SCHEMA = "opsx-debt-board-open-items-v1"
TRUSTED_PROVENANCE = "OPSX_DEBT_BOARD_INDEX"

DebtLoadStatus = Literal["TRUSTED", "MISSING", "CORRUPT", "UNTRUSTED"]


@dataclass(frozen=True)
class DebtBoardLoad:
    status: DebtLoadStatus
    items: list[dict[str, Any]]
    reason: str = ""


def _normalize_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [row for row in value["items"] if isinstance(row, dict)]
    return []


def load_trusted_debt_board(repo_root: Path) -> DebtBoardLoad:
    path = repo_root / OPEN_DEBT_PATH
    if not path.is_file():
        return DebtBoardLoad(status="MISSING", items=[], reason="debt_board_file_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DebtBoardLoad(status="CORRUPT", items=[], reason="debt_board_json_corrupt")
    if isinstance(raw, list):
        return DebtBoardLoad(status="UNTRUSTED", items=[], reason="legacy_list_schema_untrusted")
    if not isinstance(raw, dict):
        return DebtBoardLoad(status="CORRUPT", items=[], reason="debt_board_not_object")
    if str(raw.get("schema_version") or "") != TRUSTED_SCHEMA:
        return DebtBoardLoad(status="UNTRUSTED", items=[], reason="schema_version_mismatch")
    if str(raw.get("trust_provenance") or "") != TRUSTED_PROVENANCE:
        return DebtBoardLoad(status="UNTRUSTED", items=[], reason="trust_provenance_invalid")
    return DebtBoardLoad(status="TRUSTED", items=_normalize_items(raw), reason="ok")


def open_debt_for_mission(repo_root: Path, *, mission_id: str, execution_id: str) -> list[dict[str, Any]]:
    loaded = load_trusted_debt_board(repo_root)
    if loaded.status != "TRUSTED":
        return []
    open_rows: list[dict[str, Any]] = []
    for row in loaded.items:
        if str(row.get("status") or "").upper() not in {"OPEN", "ACTIVE"}:
            continue
        if row.get("mission_id") == mission_id or row.get("execution_id") == execution_id:
            open_rows.append(row)
    return open_rows


def verify_no_open_debt(
    repo_root: Path, *, mission_id: str, execution_id: str
) -> tuple[str, list[dict[str, Any]], str]:
    """Returns (verification_status, open_rows, reason)."""
    loaded = load_trusted_debt_board(repo_root)
    if loaded.status == "MISSING":
        return "UNKNOWN", [], loaded.reason
    if loaded.status == "CORRUPT":
        return "PAUSE", [], loaded.reason
    if loaded.status == "UNTRUSTED":
        return "PAUSE", [], loaded.reason
    rows = open_debt_for_mission(repo_root, mission_id=mission_id, execution_id=execution_id)
    if rows:
        return "OPEN_DEBT", rows, "open_debt_remaining"
    return "VERIFIED_NO_OPEN_DEBT", [], "ok"
