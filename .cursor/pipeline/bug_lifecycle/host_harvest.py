"""Host /opsx-bug-team harvest: dispatch wiring + idempotent terminal consumption."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .bug_team_authority import consume_terminal_if_present
from .bug_team_transport import record_dispatch, write_execution_terminal
from .hermetic_boundary import assert_hermetic_workspace, hermetic_execution_enabled

HOST_CLI = Path(".cursor/pipeline/bug-team/harness/opsx_bug_team_host_dispatch.py")
HOST_PROVENANCE = "HOST_BUG_TEAM"


def write_host_harvest_artifacts(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
) -> None:
    """Hermetic stand-in for host-written case results (no live product repair)."""
    assert_hermetic_workspace(repo_root)
    case_root = repo_root / "opsx" / "runs" / "bug-cases" / case_id
    results = case_root / "results"
    results.mkdir(parents=True, exist_ok=True)
    green = results / f"GREEN-{finding_id}.md"
    green.write_text(
        "\n".join(
            [
                f"TEST_ID: RED-{finding_id}",
                f"BUG_CASE_ID: {case_id}",
                f"FINDING_ID: {finding_id}",
                "COMMAND: /opsx-test verify mode",
                "PRODUCTION_HASH_AFTER: host-harvest",
                "TEST_CONTENT_SHA256: host-harvest-sha",
                "STATUS: GREEN_CONFIRMED",
                "TARGETED_REGRESSIONS: none",
                f"PROVENANCE: {HOST_PROVENANCE}",
                f"WORKER_EXECUTION_ID: {execution_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    packet = results / f"BUG-TEAM-{finding_id}.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": "opsx-bug-team-packet-v1",
                "CLOSURE_READINESS": "READY_FOR_CLOSURE",
                "BLOCKING_FINDINGS": "NONE",
                "CASE_STATUS": "CLOSING",
                "mission_id": mission_id,
                "case_id": case_id,
                "finding_id": finding_id,
                "plan_id": plan_id,
                "work_item_id": work_item_id,
                "execution_id": execution_id,
                "head": head,
                "PROVENANCE": HOST_PROVENANCE,
                "EXECUTION_TERMINAL": "CONFIRMED",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_execution_terminal(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        source=HOST_PROVENANCE,
    )


def _resolve_host_cli_script(repo_root: Path) -> Path | None:
    candidate = (repo_root / HOST_CLI).resolve()
    if candidate.is_file():
        return candidate
    pipeline_root = Path(__file__).resolve().parents[1]
    fallback = pipeline_root / "bug-team" / "harness" / "opsx_bug_team_host_dispatch.py"
    if fallback.is_file():
        return fallback.resolve()
    return None


def run_host_dispatch_cli(
    repo_root: Path,
    *,
    host_args: str,
    acceptance_store_root: Path | None = None,
) -> dict[str, Any]:
    script = _resolve_host_cli_script(repo_root)
    if script is None:
        return {"STATUS": "DISPATCH_FAILED", "REASON": "missing_host_cli"}
    cmd = [sys.executable, str(script), "--args", host_args, "--repo", str(repo_root)]
    if acceptance_store_root is not None:
        cmd.extend(["--acceptance-store", str(acceptance_store_root)])
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=script.parent.parent)
    if proc.returncode != 0:
        return {
            "STATUS": "DISPATCH_FAILED",
            "REASON": "host_cli_nonzero_exit",
            "EXIT_CODE": proc.returncode,
            "STDERR": proc.stderr,
        }
    if not proc.stdout.strip():
        return {"STATUS": "DISPATCH_FAILED", "REASON": "missing_host_receipt"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"STATUS": "DISPATCH_FAILED", "REASON": "invalid_host_receipt"}
    if not isinstance(payload, dict):
        return {"STATUS": "DISPATCH_FAILED", "REASON": "invalid_host_receipt"}
    return payload


def harvest_after_dispatch(
    repo_root: Path,
    request: dict[str, Any],
    *,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
    production: bool,
    acceptance_store_root: Path | None = None,
    simulate_host_write: bool = False,
) -> dict[str, Any]:
    mission_id = str(request.get("mission") or "")
    execution_id = str(request.get("execution_id") or "")
    head = str(request.get("head") or "")
    dispatch = record_dispatch(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        case_id=case_id,
        finding_id=finding_id,
        plan_id=plan_id,
        work_item_id=work_item_id,
    )
    if dispatch.get("STATUS") == "FAIL_CLOSED":
        return dispatch
    if str(dispatch.get("TEMPORAL_NESTED_START", "NO")).upper() == "YES":
        return {"STATUS": "FAIL_CLOSED", "REASON": "nested_temporal_forbidden"}
    host_args = str(dispatch.get("HOST_ARGS") or f"case={case_id} mission={mission_id}")
    if acceptance_store_root is not None:
        host_receipt = run_host_dispatch_cli(
            repo_root,
            host_args=host_args,
            acceptance_store_root=acceptance_store_root,
        )
        if host_receipt.get("STATUS") == "DISPATCH_FAILED":
            return {
                "STATUS": "FAIL_CLOSED",
                "REASON": "host_dispatch_failed",
                "HOST": host_receipt,
            }
    if simulate_host_write:
        if production:
            return {
                "STATUS": "FAIL_CLOSED",
                "REASON": "synthetic_host_write_forbidden_in_production",
            }
        if not hermetic_execution_enabled(repo_root):
            return {
                "STATUS": "FAIL_CLOSED",
                "REASON": "synthetic_host_write_requires_hermetic_workspace",
            }
        write_host_harvest_artifacts(
            repo_root,
            mission_id=mission_id,
            execution_id=execution_id,
            head=head,
            case_id=case_id,
            finding_id=finding_id,
            plan_id=plan_id,
            work_item_id=work_item_id,
        )
    consumed = consume_terminal_if_present(
        repo_root,
        request,
        case_id=case_id,
        finding_id=finding_id,
        production=production,
    )
    if consumed is None:
        return {"STATUS": "FAIL_CLOSED", "REASON": "harvest_not_consumed"}
    if consumed.get("STATUS") == "FAIL_CLOSED":
        return consumed
    return {**consumed, "DISPATCH_ID": dispatch.get("DISPATCH_ID"), "HARVEST": "YES"}
