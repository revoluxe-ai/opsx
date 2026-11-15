"""S9 debt board trusted-source gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle.debt_board import load_trusted_debt_board, verify_no_open_debt
from bug_lifecycle.evolution_handoff import write_debt_board_verified


def _debt_path(root: Path) -> Path:
    path = root / "opsx/runs/debt-board/open-items.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_missing_debt_board_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    status, rows, _ = verify_no_open_debt(root, mission_id="M", execution_id="E")
    assert status == "UNKNOWN"
    assert rows == []
    out = write_debt_board_verified(root, mission_id="M", execution_id="E", head="h")
    assert out["STATUS"] == "PAUSE"
    assert out["DEBT_BOARD"] == "UNKNOWN"


def test_corrupt_json_pauses(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _debt_path(root).write_text("{not-json", encoding="utf-8")
    assert load_trusted_debt_board(root).status == "CORRUPT"
    out = write_debt_board_verified(root, mission_id="M", execution_id="E", head="h")
    assert out["STATUS"] == "FAIL_CLOSED"
    assert out["DEBT_BOARD"] == "PAUSE"


def test_legacy_list_untrusted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _debt_path(root).write_text(json.dumps([{"status": "OPEN"}]), encoding="utf-8")
    assert load_trusted_debt_board(root).status == "UNTRUSTED"
    out = write_debt_board_verified(root, mission_id="M", execution_id="E", head="h")
    assert out["DEBT_BOARD"] == "PAUSE"
    assert "VERIFIED" not in str(out.get("RECEIPT", ""))


def test_trusted_empty_board_verifies(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _debt_path(root).write_text(
        json.dumps(
            {
                "schema_version": "opsx-debt-board-open-items-v1",
                "trust_provenance": "OPSX_DEBT_BOARD_INDEX",
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    out = write_debt_board_verified(root, mission_id="M", execution_id="E", head="h")
    assert out["STATUS"] == "DEBT_VERIFIED"
