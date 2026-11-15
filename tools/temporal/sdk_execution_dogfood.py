#!/usr/bin/env python3
"""Orchestration-level dogfood for Cursor SDK execution migration slice.

Runs CursorSdkExecutionWorkflow → execute_cursor → CursorExecutor → Cursor SDK.
Writes machine-readable evidence JSON under tools/temporal/.dogfood-evidence/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parent
GAME_ROOT = ROOT.parents[1]
EVIDENCE_DIR = ROOT / ".dogfood-evidence"

from config import TEMPORAL_TASK_QUEUE
from cursor_execution_activity import execute_cursor, reconcile_cursor, reset_cursor_execution_config

_CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
if str(_CURSOR_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_CURSOR_EXECUTOR_ROOT))
from cursor_executor.auth import resolve_auth  # noqa: E402
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow import CursorSdkExecutionWorkflow


def _route() -> dict:
    return {
        "model_id": "gpt-5.6-sol",
        "params": {"context": "272k", "reasoning": "high", "fast": "false"},
    }


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _product_git_status(repo: Path) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip()


async def _run_workflow(payload: dict, workflow_id: str) -> dict:
    reset_cursor_execution_config()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[CursorSdkExecutionWorkflow],
            activities=[execute_cursor, reconcile_cursor],
        ):
            return await env.client.execute_workflow(
                CursorSdkExecutionWorkflow.run,
                payload,
                id=workflow_id,
                task_queue=TEMPORAL_TASK_QUEUE,
            )


async def run_readonly(repo: Path, execution_id: str) -> dict:
    payload = {
        "execution_id": execution_id,
        "orchestration_execution_id": execution_id,
        "correlation_id": f"sdk-migration-readonly-{_utc_slug()}",
        "workspace": str(repo.resolve()),
        "runtime": "LOCAL",
        "model_route": _route(),
        "prompt": "Return exactly:\n\nOPSX_SDK_PROCESS_READONLY_OK",
        "write_policy": "READ_ONLY",
        "timeout_seconds": 600,
        "metadata": {"slice": "sdk-execution-migration-v0", "mode": "READ_ONLY"},
    }
    before = _product_git_status(repo)
    workflow_id = f"cursor-sdk-readonly-{_utc_slug()}"
    wf_result = await _run_workflow(payload, workflow_id)
    after = _product_git_status(repo)
    cursor = wf_result.get("cursor_execution") or {}
    retry = await _run_workflow(payload, f"{workflow_id}-retry-sim")
    retry_cursor = retry.get("cursor_execution") or {}
    return {
        "mode": "READ_ONLY",
        "workflow_id": workflow_id,
        "orchestration_execution_id": payload["orchestration_execution_id"],
        "execution_id": execution_id,
        "workflow_result": wf_result,
        "retry_simulation": {
            "same_agent_id": cursor.get("agent_id") == retry_cursor.get("agent_id"),
            "same_run_id": cursor.get("run_id") == retry_cursor.get("run_id"),
            "retry_cursor_execution": retry_cursor,
        },
        "agent_id": cursor.get("agent_id"),
        "run_id": cursor.get("run_id"),
        "requested_model_route": cursor.get("requested_model_route"),
        "observed_model_route": cursor.get("observed_model_route"),
        "sdk_status": cursor.get("sdk_status"),
        "execution_status": wf_result.get("execution_status"),
        "result_text": cursor.get("result_text"),
        "legacy_cli_invocations": cursor.get("legacy_transport_evidence", {}).get(
            "agent_cli_invocation_count"
        ),
        "product_repo_mutation_before": before,
        "product_repo_mutation_after": after,
        "product_repo_unchanged": before == after,
    }


def _mutation_file_matches(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return text.rstrip("\r\n") == expected


async def run_mutating(product_repo: Path, execution_id: str) -> dict:
    fixture_parent = ROOT / "fixtures" / "sdk_mutating_runs"
    fixture_parent.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="sdk-mut-", dir=fixture_parent))
    target = fixture / "SDK_MUTATION_PROOF.txt"
    prompt = (
        f"In workspace {fixture}, create file SDK_MUTATION_PROOF.txt with exact "
        "content OPSX_SDK_MUTATING_OK and nothing else. Do not modify any other directory."
    )
    payload = {
        "execution_id": execution_id,
        "orchestration_execution_id": execution_id,
        "correlation_id": f"sdk-migration-mutating-{_utc_slug()}",
        "workspace": str(fixture.resolve()),
        "runtime": "LOCAL",
        "model_route": _route(),
        "prompt": prompt,
        "write_policy": "MUTATING",
        "timeout_seconds": 900,
        "metadata": {"slice": "sdk-execution-migration-v0", "mode": "MUTATING"},
    }
    product_before = _product_git_status(product_repo)
    wf_result = await _run_workflow(payload, f"cursor-sdk-mutating-{_utc_slug()}")
    product_after = _product_git_status(product_repo)
    cursor = wf_result.get("cursor_execution") or {}
    observed = _mutation_file_matches(target, "OPSX_SDK_MUTATING_OK")
    evidence = {
        "mode": "MUTATING",
        "fixture_workspace": str(fixture),
        "execution_id": execution_id,
        "agent_id": cursor.get("agent_id"),
        "run_id": cursor.get("run_id"),
        "expected_mutation": "SDK_MUTATION_PROOF.txt:OPSX_SDK_MUTATING_OK",
        "observed_mutation": observed,
        "workflow_result": wf_result,
        "legacy_cli_invocations": cursor.get("legacy_transport_evidence", {}).get(
            "agent_cli_invocation_count"
        ),
        "product_repo_unchanged": product_before == product_after,
        "product_repo_mutation_before": product_before,
        "product_repo_mutation_after": product_after,
    }
    shutil.rmtree(fixture, ignore_errors=True)
    return evidence


async def main() -> int:
    parser = argparse.ArgumentParser(description="SDK execution migration dogfood")
    parser.add_argument("--repo", default=str(GAME_ROOT))
    parser.add_argument("--mode", choices=["readonly", "mutating", "all"], default="all")
    parser.add_argument("--execution-prefix", default="sdk-migration-v0")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    if not resolve_auth().api_key:
        print("CURSOR_API_KEY is required for live dogfood", file=sys.stderr)
        return 2

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _utc_slug()
    bundle: dict = {"stamp": stamp, "repo": str(repo)}

    if args.mode in ("readonly", "all"):
        ro_id = f"{args.execution_prefix}-readonly-{uuid.uuid4().hex[:8]}"
        bundle["read_only"] = await run_readonly(repo, ro_id)
    if args.mode in ("mutating", "all"):
        mut_id = f"{args.execution_prefix}-mutating-{uuid.uuid4().hex[:8]}"
        bundle["mutating"] = await run_mutating(repo, mut_id)

    out_path = EVIDENCE_DIR / f"SDK_EXECUTION_DOGFOOD_{stamp}.json"
    out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    print(json.dumps({"evidence_path": str(out_path), "bundle": bundle}, indent=2))
    ro = bundle.get("read_only") or {}
    mut = bundle.get("mutating") or {}
    ok_ro = ro.get("execution_status") == "EXECUTION_SUCCEEDED" and ro.get("product_repo_unchanged")
    ok_mut = (
        mut.get("observed_mutation")
        and mut.get("product_repo_unchanged")
        and (mut.get("workflow_result") or {}).get("execution_status") == "EXECUTION_SUCCEEDED"
        and ((mut.get("workflow_result") or {}).get("cursor_execution") or {}).get(
            "terminal_class"
        )
        == "SUCCEEDED"
    )
    if args.mode == "readonly":
        return 0 if ok_ro else 1
    if args.mode == "mutating":
        return 0 if ok_mut else 1
    return 0 if ok_ro and ok_mut else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
