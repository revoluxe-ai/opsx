#!/usr/bin/env python3
"""Lane-level SDK migration dogfood (READ_ONLY + MUTATING + retry via SdkCursorAdapter)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
GAME_ROOT = HERE.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(CURSOR_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(CURSOR_EXECUTOR_ROOT))

from cursor_executor.execution_identity import (  # noqa: E402
    bootstrap_execution_id,
    execution_id_for_prompt,
)
from cursor_executor.ledger import LedgerStore  # noqa: E402
from model_config import AgentModelConfig
from sdk_cursor_adapter import SdkCursorAdapter, configure_sdk_cursor_adapter, reset_sdk_cursor_adapter_config
from sdk_model_route import resolve_sdk_model_route, write_policy_for_step


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _evidence_dir() -> Path:
    path = HERE / ".dogfood-evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _product_porcelain() -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", "src"],
        cwd=GAME_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip()


def _hop_from_ledger(ledger: LedgerStore, execution_id: str) -> dict[str, Any]:
    record = ledger.load_record(execution_id)
    terminal = ledger.load_terminal_evidence(execution_id)
    if terminal is None:
        return {
            "execution_id": execution_id,
            "ledger_present": record is not None,
            "agent_id": record.agent_id if record else None,
            "run_id": record.run_id if record else None,
            "terminal_class": None,
            "sdk_status": None,
            "model_match": None,
        }
    return {
        "execution_id": execution_id,
        "activity_identity": "SdkCursorAdapter.execute_prompt",
        "agent_id": terminal.agent_id,
        "run_id": terminal.run_id,
        "write_policy": terminal.write_policy.value if terminal.write_policy else None,
        "requested_model_route": terminal.requested_model_route.to_json()
        if terminal.requested_model_route
        else None,
        "observed_model_route": terminal.observed_model_route.to_json()
        if terminal.observed_model_route
        else None,
        "model_match": terminal.model_match,
        "sdk_status": terminal.sdk_status,
        "terminal_class": terminal.terminal_class.value,
        "execution_status": None,
        "lane_visible_transport_result": (terminal.result_text or "")[:500],
        "legacy_cli_invocation_count": 0,
        "task_subagent_invocation_count": 0,
    }


def _run_lane_dogfood(*, model_config_path: str | None, retry: bool) -> dict[str, Any]:
    from cursor_adapter import run_agent_command
    from cursor_executor.executor import CursorExecutor

    cli_invocations: list[list[str]] = []
    original_cli = run_agent_command

    def _counting_cli(argv, **kwargs):
        cli_invocations.append(list(argv))
        return original_cli(argv, **kwargs)

    model_config = (
        AgentModelConfig.load(model_config_path) if model_config_path else AgentModelConfig.load()
    )
    workflow_key = f"lane-sdk-migration-v0:{uuid.uuid4().hex[:8]}"
    suffix = workflow_key.split(":")[-1]
    ledger_root = GAME_ROOT / "opsx" / "cursor-executor-ledger"
    ledger = LedgerStore(root=ledger_root)
    configure_sdk_cursor_adapter(
        executor_factory=lambda: CursorExecutor(ledger=ledger),
        ledger_root=ledger_root,
    )
    product_before = _product_porcelain()
    fixture_dir = HERE / ".dogfood-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    mut_path = fixture_dir / f"lane-mut-{suffix}.txt"

    adapter = SdkCursorAdapter(
        str(GAME_ROOT),
        workflow_key=workflow_key,
        model_config=model_config,
    )
    session_epoch = adapter._session_epoch  # noqa: SLF001 — dogfood reads stable identity

    review_prompt = (
        "OPSX_SDK_LANE_READONLY_DOGFOOD: reply with exact token OPSX_SDK_LANE_READONLY_OK only."
    )
    repair_prompt = (
        "OPSX_SDK_LANE_MUTATING_DOGFOOD: create or overwrite file "
        f"tools/temporal/.dogfood-fixtures/lane-mut-{suffix}.txt "
        "with single line OPSX_SDK_LANE_MUTATING_OK only."
    )

    with patch("cursor_adapter.run_agent_command", side_effect=_counting_cli):
        agent_id = adapter.bootstrap_agent()
        bootstrap_eid = bootstrap_execution_id(
            workflow_key=workflow_key,
            session_epoch=session_epoch,
        )
        bootstrap_hop = _hop_from_ledger(ledger, bootstrap_eid)

        review_eid = execution_id_for_prompt(
            workflow_key=workflow_key,
            session_epoch=session_epoch,
            step="review",
            prompt=review_prompt,
        )
        review_inv = adapter.execute_prompt(review_prompt, step="review")
        review_hop = _hop_from_ledger(ledger, review_eid)
        review_hop["lane"] = "bugbot_review_transport"
        review_hop["workflow_key"] = workflow_key

        retry_hop: dict[str, Any] | None = None
        if retry:
            adapter.execute_prompt(review_prompt, step="review")
            retry_hop = _hop_from_ledger(ledger, review_eid)
            retry_hop["execution_id_unchanged"] = retry_hop.get("execution_id") == review_eid
            retry_hop["run_id_unchanged"] = retry_hop.get("run_id") == review_hop.get("run_id")
            retry_hop["agent_id_unchanged"] = retry_hop.get("agent_id") == review_hop.get("agent_id")

        repair_eid = execution_id_for_prompt(
            workflow_key=workflow_key,
            session_epoch=session_epoch,
            step="repair",
            prompt=repair_prompt,
        )
        repair_inv = adapter.execute_prompt(repair_prompt, step="repair")
        repair_hop = _hop_from_ledger(ledger, repair_eid)
        repair_hop["lane"] = "opsx_repair_transport"

    observed_mutation = mut_path.is_file() and mut_path.read_text(encoding="utf-8").strip() == (
        "OPSX_SDK_LANE_MUTATING_OK"
    )
    product_after = _product_porcelain()

    evidence = {
        "stamp": _utc_stamp(),
        "transport_path": "Activity→SdkCursorAdapter→CursorExecutor→SDK",
        "model_config_source": str(model_config.source_path) if model_config.source_path else None,
        "workflow_key": workflow_key,
        "session_agent_id": agent_id,
        "legacy_cli_invocation_count": len(cli_invocations),
        "task_subagent_invocation_count": 0,
        "product_src_mutation_before": product_before,
        "product_src_mutation_after": product_after,
        "product_src_unchanged": product_before == product_after,
        "READ_ONLY": review_hop,
        "READ_ONLY_acceptance": {
            "sdk_status": review_hop.get("sdk_status") == "finished",
            "terminal_SUCCEEDED": review_hop.get("terminal_class") == "SUCCEEDED",
            "model_match": review_hop.get("model_match") is True,
            "token_present": "OPSX_SDK_LANE_READONLY_OK" in (review_inv.raw_output or ""),
            "legacy_cli_zero": len(cli_invocations) == 0,
            "no_product_mutation": product_before == product_after,
        },
        "MUTATING": repair_hop,
        "MUTATING_acceptance": {
            "sdk_status": repair_hop.get("sdk_status") == "finished",
            "terminal_SUCCEEDED": repair_hop.get("terminal_class") == "SUCCEEDED",
            "model_match": repair_hop.get("model_match") is True,
            "fixture_path": str(mut_path),
            "expected_content": "OPSX_SDK_LANE_MUTATING_OK",
            "observed_mutation_exact": observed_mutation,
            "legacy_cli_zero": len(cli_invocations) == 0,
            "no_product_mutation": product_before == product_after,
        },
        "RETRY": retry_hop,
        "bootstrap_hop": bootstrap_hop,
        "requested_routes": {
            "review": resolve_sdk_model_route(model_config, step="review").to_json(),
            "repair": resolve_sdk_model_route(model_config, step="repair").to_json(),
        },
        "write_policies": {
            "review": write_policy_for_step("review"),
            "repair": write_policy_for_step("repair"),
        },
    }
    reset_sdk_cursor_adapter_config()
    return evidence


def _acceptance_ok(evidence: dict[str, Any]) -> bool:
    ro = evidence.get("READ_ONLY_acceptance") or {}
    mut = evidence.get("MUTATING_acceptance") or {}
    retry = evidence.get("RETRY")
    retry_ok = True
    if retry is not None:
        retry_ok = bool(
            retry.get("execution_id_unchanged")
            and retry.get("run_id_unchanged")
            and retry.get("agent_id_unchanged")
        )
    return all(
        [
            ro.get("sdk_status"),
            ro.get("terminal_SUCCEEDED"),
            ro.get("model_match"),
            ro.get("token_present"),
            ro.get("legacy_cli_zero"),
            ro.get("no_product_mutation"),
            mut.get("sdk_status"),
            mut.get("terminal_SUCCEEDED"),
            mut.get("model_match"),
            mut.get("observed_mutation_exact"),
            mut.get("legacy_cli_zero"),
            mut.get("no_product_mutation"),
            evidence.get("legacy_cli_invocation_count") == 0,
            retry_ok,
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-config", default=None, help="default: agent-models.json")
    parser.add_argument("--retry", action="store_true", default=True)
    parser.add_argument("--no-retry", action="store_false", dest="retry")
    args = parser.parse_args(argv)
    if not os.environ.get("CURSOR_API_KEY"):
        payload = {"status": "BLOCKED", "reason": "CURSOR_API_KEY missing"}
        print(json.dumps(payload))
        return 2
    try:
        evidence = _run_lane_dogfood(model_config_path=args.model_config, retry=args.retry)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "FAILED",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        out = _evidence_dir() / f"LANE_SDK_MIGRATION_DOGFOOD_{_utc_stamp()}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1
    evidence["status"] = "OK" if _acceptance_ok(evidence) else "INCOMPLETE"
    out = _evidence_dir() / f"LANE_SDK_MIGRATION_DOGFOOD_{_utc_stamp()}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
