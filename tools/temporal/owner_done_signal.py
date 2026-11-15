"""PP post-completion step: owner-done-signal.

After a /tempo-* pipeline reaches a terminal workflow outcome, the operator
emits a durable signal manifest and (via the executing agent) opens the static
owner takeover page in the Owner's existing Chrome session.

Browser navigation is intentionally agent-driven (user-chrome-devtools MCP
navigate_page) so we reuse the authenticated CDP session — no new profiles.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ASSETS_DIR = HERE / "assets"
DONE_PAGE_HTML = ASSETS_DIR / "owner-done-signal.html"
OWNER_DONE_MESSAGE = "Готово, всё, я знаю, что готово, перехватываю управление"

STEP_ID = "owner-done-signal"


def done_page_path() -> Path:
    """Return the static HTML asset path (must exist)."""
    return DONE_PAGE_HTML


def done_page_file_url(*, repo: Path | None = None) -> str:
    """file:// URL for navigate_page in Owner Chrome."""
    path = done_page_path().resolve()
    if repo is not None:
        # Prefer repo-relative resolution when assets are workspace-local.
        candidate = (Path(repo) / "tools" / "temporal" / "assets" / "owner-done-signal.html").resolve()
        if candidate.is_file():
            path = candidate
    quoted = quote(path.as_posix(), safe="/:")
    # Windows drive letters need file:///C:/... form.
    if len(path.as_posix()) >= 2 and path.as_posix()[1] == ":":
        return f"file:///{quoted}"
    return f"file://{quoted}"


def is_terminal_outcome(outcome: str | None, spec: dict[str, Any]) -> bool:
    """True when workflow result status is a terminal PP outcome."""
    if not outcome:
        return False
    ok = set(spec.get("ok_statuses") or [])
    fail = set(spec.get("process_fail_statuses") or [])
    stop = set(spec.get("stop_statuses") or [])
    return outcome in ok or outcome in fail or outcome in stop


def classify_terminal_bucket(
    outcome: str | None,
    spec: dict[str, Any],
    *,
    operator_status: str | None = None,
) -> str | None:
    """Map outcome to PASS | FAIL | BLOCKED | STOP for Owner reporting."""
    if operator_status == "BLOCKED":
        return "BLOCKED"
    if not outcome:
        return None
    ok = set(spec.get("ok_statuses") or [])
    fail = set(spec.get("process_fail_statuses") or [])
    stop = set(spec.get("stop_statuses") or [])
    if outcome in ok:
        return "PASS"
    if outcome in fail:
        if "BLOCKED" in outcome or outcome.endswith("_BLOCKED"):
            return "BLOCKED"
        return "FAIL"
    if outcome in stop:
        return "STOP"
    return "STOP"


def signal_manifest_path(repo: Path, workflow_id: str | None) -> Path:
    key = (workflow_id or "unknown").replace("/", "-")
    from run_store import durable_gate_dir

    return durable_gate_dir(repo) / "owner-done-signal" / f"{key}.json"


def build_signal_manifest(
    *,
    repo: Path,
    pipeline_id: str,
    workflow_id: str | None,
    outcome: str | None,
    terminal_bucket: str | None,
    resolved: dict[str, str],
    done_page_url: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "step_id": STEP_ID,
        "message": OWNER_DONE_MESSAGE,
        "done_page_url": done_page_url,
        "done_page_path": str(done_page_path().resolve()),
        "pipeline": pipeline_id,
        "workflow_id": workflow_id,
        "outcome": outcome,
        "terminal_bucket": terminal_bucket,
        "change": resolved.get("change"),
        "base": resolved.get("base"),
        "head": resolved.get("head"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "browser_action": {
            "mcp_namespace": "user-chrome-devtools",
            "tool": "navigate_page",
            "args": {"url": done_page_url, "type": "url"},
        },
    }


def run_owner_done_signal(
    *,
    repo: Path,
    pipeline_id: str,
    spec: dict[str, Any],
    workflow_id: str | None,
    outcome: str | None,
    resolved: dict[str, str],
    operator_status: str | None = None,
) -> dict[str, Any]:
    """Execute owner-done-signal PP step (manifest + URL; browser via agent)."""
    terminal_bucket = classify_terminal_bucket(
        outcome, spec, operator_status=operator_status
    )
    if not is_terminal_outcome(outcome, spec) and operator_status != "BLOCKED":
        return {
            "step_id": STEP_ID,
            "skipped": True,
            "reason": "not_terminal",
            "outcome": outcome,
        }

    url = done_page_file_url(repo=repo)
    manifest = build_signal_manifest(
        repo=repo,
        pipeline_id=pipeline_id,
        workflow_id=workflow_id,
        outcome=outcome,
        terminal_bucket=terminal_bucket,
        resolved=resolved,
        done_page_url=url,
    )
    dest = signal_manifest_path(repo, workflow_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "step_id": STEP_ID,
        "skipped": False,
        "terminal_bucket": terminal_bucket,
        "done_page_url": url,
        "manifest_path": str(dest),
        "message": OWNER_DONE_MESSAGE,
        "browser_action": manifest["browser_action"],
    }


def run_post_completion_steps(
    *,
    repo: Path,
    pipeline_id: str,
    spec: dict[str, Any],
    workflow_id: str | None,
    outcome: str | None,
    resolved: dict[str, str],
    operator_status: str | None = None,
) -> list[dict[str, Any]]:
    """Run configured post_completion_steps from pipelines.json."""
    steps = spec.get("post_completion_steps") or []
    results: list[dict[str, Any]] = []
    for step in steps:
        step_id = step if isinstance(step, str) else step.get("id")
        if step_id == STEP_ID:
            results.append(
                run_owner_done_signal(
                    repo=repo,
                    pipeline_id=pipeline_id,
                    spec=spec,
                    workflow_id=workflow_id,
                    outcome=outcome,
                    resolved=resolved,
                    operator_status=operator_status,
                )
            )
    return results
