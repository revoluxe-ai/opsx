"""PP browser-check UX: optional post-smoke Chrome release page.

Owner GO / Chrome-focus pause before smoke is **retired** (`owner-chrome-focus-gate`).
`/tempo-verification` does not wait for chat GO.

Distinct from:
- owner-done-signal — full pipeline terminal takeover

Pre templates remain for compatibility; they are not a live pause.
Post: agent may open owner-browser-check-release.html after smoke executor finishes
(via user-chrome-devtools navigate_page when MCP exists).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ASSETS_DIR = HERE / "assets"
RELEASE_HTML = ASSETS_DIR / "owner-browser-check-release.html"

STEP_ID = "owner-browser-check-gate"
GO_STEP_ID = "owner-browser-check-go"
RELEASE_STEP_ID = "owner-browser-check-release"

OWNER_BROWSER_CHECK_RELEASE_MESSAGE = (
    "Браузерная проверка завершена — можно снова пользоваться Chrome"
)

DEFAULT_DURATION_ESTIMATE = "~3–5 минут"
DEFAULT_PLAY_URL = "http://127.0.0.1:8080/novel/null-parallele/play"

_GO_TOKEN_RE = re.compile(r"\bGO\b", re.IGNORECASE)

_SMOKE_CAPABILITY = "smoke_acceptance"
_BROWSER_EXECUTOR = "opsx-browser-check"


def is_browser_check_assignment(assignment: dict[str, Any] | None) -> bool:
    """True when assignment is a smoke / opsx-browser-check executor."""
    if not assignment:
        return False
    capability = str(assignment.get("capability_id") or "")
    executor = str(assignment.get("executor") or "").lstrip("/")
    required_check_id = str(assignment.get("required_check_id") or "")
    if capability == _SMOKE_CAPABILITY:
        return True
    if executor == _BROWSER_EXECUTOR:
        return True
    if "smoke_acceptance" in required_check_id:
        return True
    return False


def release_page_path() -> Path:
    return RELEASE_HTML


def release_page_file_url(*, repo: Path | None = None) -> str:
    path = release_page_path().resolve()
    if repo is not None:
        candidate = (
            Path(repo) / "tools" / "temporal" / "assets" / "owner-browser-check-release.html"
        ).resolve()
        if candidate.is_file():
            path = candidate
    quoted = quote(path.as_posix(), safe="/:")
    if len(path.as_posix()) >= 2 and path.as_posix()[1] == ":":
        return f"file:///{quoted}"
    return f"file://{quoted}"


def _safe_id(required_check_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", required_check_id)


def release_manifest_path(
    repo: Path | str,
    required_check_id: str,
    assignment_epoch: int,
) -> Path:
    from run_store import durable_gate_dir

    key = f"{_safe_id(required_check_id)}_epoch{assignment_epoch}"
    return durable_gate_dir(repo) / "owner-browser-check-gate" / "releases" / f"{key}.json"


def go_ack_path(
    repo: Path | str,
    *,
    required_check_id: str,
    assignment_epoch: int,
) -> Path:
    from run_store import durable_gate_dir

    key = f"{_safe_id(required_check_id)}_epoch{assignment_epoch}"
    return durable_gate_dir(repo) / "owner-browser-check-gate" / "go-acks" / f"{key}.json"


def owner_chat_contains_go(text: str) -> bool:
    """True when Owner chat message contains GO token (case-insensitive)."""
    return bool(_GO_TOKEN_RE.search(text or ""))


def is_go_acknowledged(repo: Path, assignment: dict[str, Any]) -> bool:
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    if not required_check_id or epoch <= 0:
        return False
    path = go_ack_path(repo, required_check_id=required_check_id, assignment_epoch=epoch)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        data.get("step_id") == GO_STEP_ID
        and data.get("required_check_id") == required_check_id
        and int(data.get("assignment_epoch") or 0) == epoch
    )


def write_go_ack(
    repo: Path,
    *,
    required_check_id: str,
    assignment_epoch: int,
    change: str | None = None,
    source: str | None = None,
) -> Path:
    path = go_ack_path(
        repo,
        required_check_id=required_check_id,
        assignment_epoch=assignment_epoch,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "step_id": GO_STEP_ID,
        "required_check_id": required_check_id,
        "assignment_epoch": assignment_epoch,
        "change": change,
        "source": source,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def is_pre_smoke_acknowledged(repo: Path, assignment: dict[str, Any]) -> bool:
    """True when Owner gave GO via focus-ack CLI or owner-browser-check-go."""
    from owner_chrome_focus_gate import is_focus_acknowledged

    return is_focus_acknowledged(repo, assignment) or is_go_acknowledged(repo, assignment)


def format_chat_instruction(
    *,
    change: str,
    assignment: dict[str, Any] | None = None,
    duration_estimate: str = DEFAULT_DURATION_ESTIMATE,
    play_url: str = DEFAULT_PLAY_URL,
) -> dict[str, Any]:
    """Structured Owner chat instruction for agents to post verbatim in chat."""
    required_check_id = ""
    if assignment:
        required_check_id = str(assignment.get("required_check_id") or "")
    play_url = (play_url or DEFAULT_PLAY_URL).strip()
    body = (
        "## Браузерная проверка — действия Owner\n\n"
        "**URL игры (откройте в Chrome):**\n\n"
        f"```\n{play_url}\n```\n\n"
        "**Что сделать:**\n"
        "1. Переключитесь на **Chrome с игрой** по URL выше (или уже открытой вкладке play).\n"
        "2. Держите окно Chrome **на переднем плане** — не сворачивайте и не уводите в фон.\n"
        "3. Не переключайте вкладки во время visibility-проверки (L2_trigger).\n"
        "4. Когда готовы — напишите в чат **GO** (регистр не важен).\n\n"
        f"**Сколько займёт:** {duration_estimate} "
        "(агент поднимет serve/API при необходимости и пройдёт сценарии в браузере).\n\n"
        "**Зачем:** smoke-проверка требует **видимой** вкладки игры, иначе "
        "visibility resume-hydrate не пройдёт. После GO агент **кликнет окно Chrome** "
        "(физический OS-клик), а не только вставит URL через navigate.\n\n"
        "**Пока не напишете GO** — браузерная проверка **не запустится**.\n\n"
        f"**Change:** `{change}`"
        + (f" · **Check:** `{required_check_id}`" if required_check_id else "")
    )
    return {
        "step_id": STEP_ID,
        "phase": "pre",
        "change": change,
        "required_check_id": required_check_id or None,
        "play_url": play_url,
        "duration_estimate": duration_estimate,
        "owner_actions": [
            "focus_game_chrome_window",
            "keep_chrome_foreground",
            "no_tab_switch_during_l2",
            "say_go_in_chat",
        ],
        "go_token": "GO",
        "markdown": body,
        "plain_summary": (
            f"Owner: кликни Chrome с игрой, держи на переднем плане ~{duration_estimate.strip('~')}, "
            "затем напиши GO — иначе smoke не запустится."
        ),
    }


def format_release_chat_message(durable_result_status: str | None = None) -> str:
    """Owner-facing chat line after browser check (PASS/FAIL/BLOCKED)."""
    raw = (durable_result_status or "").strip().upper()
    if raw in ("PASS", "BROWSER_CHECK_PASS"):
        label = "PASS"
    elif raw in ("FAIL", "BROWSER_CHECK_FAIL"):
        label = "FAIL"
    elif raw in ("BLOCKED", "BROWSER_CHECK_BLOCKED"):
        label = "BLOCKED"
    else:
        label = "ЗАВЕРШЕНО"
    return f"Браузерная проверка завершена ({label}). Можно пользоваться браузером."


def build_release_manifest(
    *,
    repo: Path,
    change: str,
    assignment: dict[str, Any],
    release_page_url: str,
    durable_result_status: str | None = None,
) -> dict[str, Any]:
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    chat_message = format_release_chat_message(durable_result_status)
    return {
        "schema_version": 1,
        "step_id": RELEASE_STEP_ID,
        "message": OWNER_BROWSER_CHECK_RELEASE_MESSAGE,
        "chat_message": chat_message,
        "release_page_url": release_page_url,
        "release_page_path": str(release_page_path().resolve()),
        "change": change,
        "required_check_id": required_check_id,
        "assignment_epoch": epoch,
        "capability_id": assignment.get("capability_id"),
        "executor": assignment.get("executor"),
        "durable_result_status": durable_result_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "distinct_from": "owner-done-signal",
        "browser_action": {
            "mcp_namespace": "user-chrome-devtools",
            "tool": "navigate_page",
            "args": {"url": release_page_url, "type": "url"},
        },
    }


def run_browser_check_release(
    *,
    repo: Path,
    change: str,
    assignment: dict[str, Any],
    durable_result_status: str | None = None,
) -> dict[str, Any]:
    """Emit post-browser-check release manifest (agent opens page via MCP)."""
    if not is_browser_check_assignment(assignment):
        return {
            "step_id": RELEASE_STEP_ID,
            "skipped": True,
            "reason": "not_browser_check",
        }
    url = release_page_file_url(repo=repo)
    manifest = build_release_manifest(
        repo=repo,
        change=change,
        assignment=assignment,
        release_page_url=url,
        durable_result_status=durable_result_status,
    )
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    dest = release_manifest_path(repo, required_check_id, epoch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    chat_message = format_release_chat_message(durable_result_status)
    return {
        "step_id": RELEASE_STEP_ID,
        "skipped": False,
        "release_page_url": url,
        "manifest_path": str(dest),
        "message": OWNER_BROWSER_CHECK_RELEASE_MESSAGE,
        "chat_message": chat_message,
        "browser_action": manifest["browser_action"],
        "required_check_id": required_check_id,
        "assignment_epoch": epoch,
        "durable_result_status": durable_result_status,
    }


def browser_check_post_prompt_lines(*, repo: Path, change: str) -> list[str]:
    """Append to bounded executor prompt for opsx-browser-check post step."""
    url = release_page_file_url(repo=repo)
    sample = format_release_chat_message("PASS")
    return [
        "",
        "BROWSER_CHECK_POST_RELEASE:",
        "  After writing BOUNDED_CHECK_RESULT (PASS/FAIL/BLOCKED):",
        f"  1. user-chrome-devtools navigate_page url={url}",
        "  2. Post in chat (replace PASS/FAIL with actual STATUS):",
        f"     «{sample}»",
        "  Distinct from owner-done-signal (full pipeline terminal only).",
    ]


__all__ = [
    "STEP_ID",
    "GO_STEP_ID",
    "RELEASE_STEP_ID",
    "OWNER_BROWSER_CHECK_RELEASE_MESSAGE",
    "DEFAULT_DURATION_ESTIMATE",
    "DEFAULT_PLAY_URL",
    "_BROWSER_EXECUTOR",
    "_SMOKE_CAPABILITY",
    "is_browser_check_assignment",
    "release_page_path",
    "release_page_file_url",
    "go_ack_path",
    "owner_chat_contains_go",
    "is_go_acknowledged",
    "write_go_ack",
    "is_pre_smoke_acknowledged",
    "format_chat_instruction",
    "format_release_chat_message",
    "run_browser_check_release",
    "browser_check_post_prompt_lines",
]
