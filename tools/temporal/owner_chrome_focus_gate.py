"""Retired Owner Chrome GO/focus pause (legacy).

`/tempo-verification` no longer waits for Owner GO or a focus ack before
`smoke_acceptance` / `opsx-browser-check`. Helpers remain for CLI compatibility
and historical ack files; live scheduling never pauses on this gate.

Durable ack path (unused by the live loop):
`opsx/runs/temporal/gates/owner-chrome-focus-gate/`
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from verification_engine import load_ledger

HERE = Path(__file__).resolve().parent
ASSETS_DIR = HERE / "assets"
FOCUS_GATE_HTML = ASSETS_DIR / "owner-chrome-focus-gate.html"

STEP_ID = "owner-chrome-focus-gate"
OWNER_FOCUS_MESSAGE = (
    "Кликни окно Chrome с игрой, затем напиши GO в чате — агент продолжит"
)
TERMINAL_STATUS = "OWNER_CHROME_FOCUS_GATE"
RETIRED_OWNER_GO = True
RETIRED_SKIP_REASON = "retired_owner_go_not_required"


def focus_gate_page_path() -> Path:
    return FOCUS_GATE_HTML


def focus_gate_page_file_url(*, repo: Path | None = None) -> str:
    path = focus_gate_page_path().resolve()
    if repo is not None:
        candidate = (
            Path(repo) / "tools" / "temporal" / "assets" / "owner-chrome-focus-gate.html"
        ).resolve()
        if candidate.is_file():
            path = candidate
    quoted = quote(path.as_posix(), safe="/:")
    if len(path.as_posix()) >= 2 and path.as_posix()[1] == ":":
        return f"file:///{quoted}"
    return f"file://{quoted}"


def _safe_id(required_check_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", required_check_id)


def ack_path(
    repo: Path | str,
    *,
    required_check_id: str,
    assignment_epoch: int,
) -> Path:
    from run_store import durable_gate_dir

    key = f"{_safe_id(required_check_id)}_epoch{assignment_epoch}"
    return durable_gate_dir(repo) / "owner-chrome-focus-gate" / "acks" / f"{key}.json"


def gate_manifest_path(repo: Path | str, required_check_id: str, assignment_epoch: int) -> Path:
    from run_store import durable_gate_dir

    key = f"{_safe_id(required_check_id)}_epoch{assignment_epoch}"
    return durable_gate_dir(repo) / "owner-chrome-focus-gate" / f"{key}.json"


def needs_owner_chrome_focus_gate(assignment: dict[str, Any] | None) -> bool:
    """Always False: Owner GO/focus pause is retired for /tempo-verification."""
    del assignment
    return False


def is_focus_acknowledged(
    repo: Path,
    assignment: dict[str, Any],
) -> bool:
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    if not required_check_id or epoch <= 0:
        return False
    path = ack_path(repo, required_check_id=required_check_id, assignment_epoch=epoch)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        data.get("step_id") == STEP_ID
        and data.get("required_check_id") == required_check_id
        and int(data.get("assignment_epoch") or 0) == epoch
    )


def write_focus_ack(
    repo: Path,
    *,
    required_check_id: str,
    assignment_epoch: int,
    change: str | None = None,
) -> Path:
    path = ack_path(
        repo,
        required_check_id=required_check_id,
        assignment_epoch=assignment_epoch,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "step_id": STEP_ID,
        "required_check_id": required_check_id,
        "assignment_epoch": assignment_epoch,
        "change": change,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _browser_check_gate():
    from owner_browser_check_gate import format_chat_instruction, is_pre_smoke_acknowledged

    return format_chat_instruction, is_pre_smoke_acknowledged


def build_gate_manifest(
    *,
    repo: Path,
    change: str,
    assignment: dict[str, Any],
    gate_page_url: str,
    resume_command: str,
) -> dict[str, Any]:
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    repo_posix = Path(repo).as_posix()
    ack_cmd = (
        f"python tools/temporal/tempo_operator.py owner-chrome-focus-ack verification "
        f"--repo {repo_posix} --change {change} "
        f"--required-check-id {required_check_id} --assignment-epoch {epoch}"
    )
    go_cmd = (
        f"python tools/temporal/tempo_operator.py owner-browser-check-go verification "
        f"--repo {repo_posix} --change {change} "
        f"--required-check-id {required_check_id} --assignment-epoch {epoch}"
    )
    format_chat_instruction, _ = _browser_check_gate()
    chat = format_chat_instruction(change=change, assignment=assignment)
    return {
        "schema_version": 1,
        "step_id": STEP_ID,
        "message": OWNER_FOCUS_MESSAGE,
        "chat_instruction": chat,
        "go_token": "GO",
        "gate_page_url": gate_page_url,
        "gate_page_path": str(focus_gate_page_path().resolve()),
        "change": change,
        "required_check_id": required_check_id,
        "assignment_epoch": epoch,
        "capability_id": assignment.get("capability_id"),
        "executor": assignment.get("executor"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resume_command": resume_command,
        "ack_command": ack_cmd,
        "go_ack_command": go_cmd,
        "browser_action": {
            "mcp_namespace": "user-chrome-devtools",
            "tool": "navigate_page",
            "args": {"url": gate_page_url, "type": "url"},
        },
    }


def run_owner_chrome_focus_gate(
    *,
    repo: Path,
    change: str,
    assignment: dict[str, Any],
) -> dict[str, Any]:
    """Never pause: Owner GO/focus gate is retired."""
    del repo, change
    return {
        "step_id": STEP_ID,
        "skipped": True,
        "reason": RETIRED_SKIP_REASON,
        "required_check_id": assignment.get("required_check_id") if assignment else None,
        "assignment_epoch": assignment.get("assignment_epoch") if assignment else None,
    }


def evaluate_pre_run_gate(repo: Path, change: str) -> dict[str, Any] | None:
    """Retired: never pause /tempo-verification for Owner Chrome GO."""
    del repo, change
    return None


def assignment_from_ledger(repo: Path, change: str) -> dict[str, Any] | None:
    try:
        ledger = load_ledger(repo, change)
    except FileNotFoundError:
        return None
    assignment = ledger.get("current_assignment")
    return assignment if isinstance(assignment, dict) else None
