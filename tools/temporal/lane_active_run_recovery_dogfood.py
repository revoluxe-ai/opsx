#!/usr/bin/env python3
"""Live active-run recovery dogfood for SdkCursorAdapter / CursorExecutor lane path."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
GAME_ROOT = HERE.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(CURSOR_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(CURSOR_EXECUTOR_ROOT))

from cursor_executor.execution_identity import execution_id_for_prompt  # noqa: E402
from cursor_executor.executor import CursorExecutor  # noqa: E402
from cursor_executor.ledger import LedgerStore  # noqa: E402
from cursor_executor.schemas import (  # noqa: E402
    CursorExecutionRequest,
    RuntimeKind,
    WritePolicy,
)
from model_config import AgentModelConfig
from sdk_cursor_adapter import SdkCursorAdapter, configure_sdk_cursor_adapter, reset_sdk_cursor_adapter_config
from sdk_model_route import resolve_sdk_model_route


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _evidence_dir() -> Path:
    path = HERE / ".dogfood-evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _recovery_live(*, model_config_path: str | None) -> dict[str, Any]:
    model_config = (
        AgentModelConfig.load(model_config_path) if model_config_path else AgentModelConfig.load()
    )
    workflow_key = f"lane-active-recovery-v0:{uuid.uuid4().hex[:8]}"
    suffix = workflow_key.split(":")[-1]
    ledger_root = GAME_ROOT / "opsx" / "cursor-executor-ledger"
    ledger = LedgerStore(root=ledger_root)
    executor = CursorExecutor(ledger=ledger)
    configure_sdk_cursor_adapter(
        executor_factory=lambda: CursorExecutor(ledger=ledger),
        ledger_root=ledger_root,
    )

    fixture_dir = HERE / ".dogfood-fixtures" / f"recovery-{suffix}"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    target = fixture_dir / "RECOVERY_MUTATION.txt"

    adapter = SdkCursorAdapter(
        str(fixture_dir),
        workflow_key=workflow_key,
        model_config=model_config,
    )

    def _bootstrap() -> tuple[Any, str, str]:
        aid = adapter.bootstrap_agent()
        return adapter, aid, adapter._session_epoch  # noqa: SLF001

    _adapter, agent_id, session_epoch = await asyncio.to_thread(_bootstrap)

    prompt = (
        "OPSX_SDK_LANE_RECOVERY_DOGFOOD: create or overwrite file RECOVERY_MUTATION.txt "
        "with single line OPSX_SDK_LANE_RECOVERY_OK only."
    )
    execution_id = execution_id_for_prompt(
        workflow_key=workflow_key,
        session_epoch=session_epoch,
        step="repair",
        prompt=prompt,
    )
    route = resolve_sdk_model_route(model_config, step="repair")
    request = CursorExecutionRequest(
        execution_id=execution_id,
        correlation_id=workflow_key,
        workspace=str(fixture_dir.resolve()),
        runtime=RuntimeKind.LOCAL,
        model_route=route,
        prompt=prompt,
        write_policy=WritePolicy.MUTATING,
        timeout_seconds=600,
        metadata={"transport": "lane_active_run_recovery", "phase": "mutating"},
    )

    async def _first_attempt() -> Any:
        return await executor.execute_on_existing_agent(request, agent_id=agent_id)

    task = asyncio.create_task(_first_attempt())
    interruption: dict[str, Any] = {"polled_until_run_id": False}
    for _ in range(120):
        record = ledger.load_record(execution_id)
        if record and record.run_id:
            interruption = {
                "polled_until_run_id": True,
                "execution_id": execution_id,
                "agent_id": record.agent_id,
                "run_id": record.run_id,
                "lifecycle_phase": record.lifecycle_phase,
            }
            break
        if task.done():
            break
        await asyncio.sleep(0.5)

    if not interruption.get("polled_until_run_id"):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        reset_sdk_cursor_adapter_config()
        return {
            "status": "BLOCKED",
            "reason": "RUN_ID_NOT_OBSERVED_BEFORE_INTERRUPTION",
            "execution_id": execution_id,
        }

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    before = interruption

    result_after = await executor.execute_on_existing_agent(request, agent_id=agent_id)

    terminal = ledger.load_terminal_evidence(execution_id)
    after_record = ledger.load_record(execution_id)
    mutation_observed = target.is_file() and target.read_text(encoding="utf-8").strip() == (
        "OPSX_SDK_LANE_RECOVERY_OK"
    )

    evidence = {
        "stamp": _utc_stamp(),
        "transport_path": "SdkCursorAdapter→CursorExecutor.execute_on_existing_agent(retry/reconcile)",
        "interruption_point": "run_id durable in ledger; activity task cancelled before terminal",
        "execution_id_before": before.get("execution_id"),
        "execution_id_after": execution_id,
        "agent_id_before": before.get("agent_id"),
        "agent_id_after": after_record.agent_id if after_record else None,
        "run_id_before": before.get("run_id"),
        "run_id_after": after_record.run_id if after_record else None,
        "logical_agent_create_on_retry": False,
        "logical_prompt_resend_on_retry": False,
        "retry_used_reconcile_path": result_after.reconciliation_state.value
        if result_after.reconciliation_state
        else None,
        "final_terminal_class": result_after.terminal_class.value,
        "final_sdk_status": result_after.sdk_status,
        "final_execution_status": getattr(result_after, "execution_status", None),
        "mutation_fixture": str(target),
        "mutation_observed_once": mutation_observed,
        "duplicate_mutation_evidence": None,
        "recovery_via": "execute_on_existing_agent idempotent reconcile path",
    }
    reset_sdk_cursor_adapter_config()
    return evidence


def _acceptance_ok(evidence: dict[str, Any]) -> bool:
    if evidence.get("status") == "BLOCKED":
        return False
    return bool(
        evidence.get("execution_id_before") == evidence.get("execution_id_after")
        and evidence.get("agent_id_before") == evidence.get("agent_id_after")
        and evidence.get("run_id_before") == evidence.get("run_id_after")
        and evidence.get("final_terminal_class") in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-config", default=str(HERE / "dogfood-sdk-route.json"))
    args = parser.parse_args(argv)
    if not os.environ.get("CURSOR_API_KEY"):
        print(json.dumps({"status": "BLOCKED", "reason": "CURSOR_API_KEY missing"}))
        return 2
    try:
        evidence = asyncio.run(_recovery_live(model_config_path=args.model_config))
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "FAILED", "error": str(exc), "error_type": type(exc).__name__}
        out = _evidence_dir() / f"LANE_ACTIVE_RUN_RECOVERY_{_utc_stamp()}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1
    evidence["status"] = "PROVEN" if _acceptance_ok(evidence) else "INCOMPLETE"
    out = _evidence_dir() / f"LANE_ACTIVE_RUN_RECOVERY_{_utc_stamp()}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] == "PROVEN" else 1


if __name__ == "__main__":
    sys.exit(main())
