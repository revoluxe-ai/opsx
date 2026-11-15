"""Input pack Test Repair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.common.prompt_embed import embed_source_window
from agents.state import GraphState
from agents.verify.static_triage.change_slice import build_change_slice
from agents.verify.static_triage.opsx_context import build_bounded_opsx_context


def build_test_repair_input_pack(
    *,
    repository_path: str,
    item: dict[str, Any],
    state: GraphState,
    triage: dict[str, Any],
) -> dict[str, Any]:
    """Собрать JSON для test fix (bounded opsx, один finding)."""
    path = item.get("path")
    line = item.get("line")
    status, slice_text, hunk_present = build_change_slice(
        repository_path=repository_path,
        path=str(path) if path else None,
        line=int(line) if isinstance(line, int) else None,
    )
    opsx_context, context_manifest = build_bounded_opsx_context(state, item=item)
    cohort = item.get("reproduction_cohort")
    gate_raw = state.get("quality_gate_raw")
    raw_pytest = gate_raw.get("pytest") if isinstance(gate_raw, dict) else None
    gate_context = (
        {
            "exit_code": raw_pytest.get("exit_code"),
            "stdout_tail": str(raw_pytest.get("stdout_tail") or "")[-5000:],
            "stderr_tail": str(raw_pytest.get("stderr_tail") or "")[-5000:],
        }
        if isinstance(raw_pytest, dict) else None
    )
    source_window = None
    if path and isinstance(path, str):
        source_window = embed_source_window(
            Path(repository_path) / path.replace("\\", "/"),
            int(line) if isinstance(line, int) else None,
        )
    return {
        "reproduction_cohort": cohort,
        "original_full_gate": gate_context,
        "gate_run_id": state.get("quality_gates_run_id"),
        "finding": item,
        "test_triage": triage,
        "change_slice_status": status,
        "change_hunk_present": hunk_present,
        "change_slice": slice_text if status == "IN_DIFF" else None,
        "source_window": source_window,
        "opsx_context": opsx_context,
        "context_manifest": context_manifest,
    }


def pack_json(package: dict[str, Any]) -> str:
    """Сериализовать пакет для prompt."""
    return json.dumps(package, ensure_ascii=False, indent=2)
