"""Регрессии: длительные hop-проверки не блокируют event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agents.state import GraphState
from workflows.implementation.routing_contract import STAGE_APPLY_SCAFFOLD
from workflows.implementation.slice_check_workflow_receipt import (
    SliceWorkflowReceiptEvaluation,
)
from workflows.implementation.slice_pipeline import stage_hop as stage_hop_mod
from workflows.implementation.slice_pipeline.stage_hop import run_slice_stage_hop


class _NoopAgent:
    async def run(self, state: GraphState) -> dict[str, Any]:
        _ = state
        return {"apply_scaffold_status": "COMPLETE"}


@pytest.mark.asyncio
async def test_evaluate_for_hop_runs_off_main_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRE ``evaluate_for_hop`` выполняется через ``asyncio.to_thread``."""
    main = threading.main_thread()
    worker_hits: list[bool] = []

    def slow_evaluate(*_args: object, **_kwargs: object) -> SliceWorkflowReceiptEvaluation:
        worker_hits.append(threading.current_thread() is not main)
        time.sleep(0.2)
        return SliceWorkflowReceiptEvaluation(False, "RECEIPT_MISSING", ())

    monkeypatch.setattr(stage_hop_mod, "evaluate_for_hop", slow_evaluate)
    progress = 0

    async def ticker() -> None:
        nonlocal progress
        for _ in range(5):
            await asyncio.sleep(0.01)
            progress += 1

    ticker_task = asyncio.create_task(ticker())
    state: GraphState = {
        "change_id": "visual",
        "repository_path": str(Path.cwd()),
        "implementation_slice_id": "SLICE-099",
    }
    await asyncio.gather(
        run_slice_stage_hop(state, agent=_NoopAgent(), stage=STAGE_APPLY_SCAFFOLD),
        ticker_task,
    )
    assert worker_hits == [True]
    assert progress >= 3
