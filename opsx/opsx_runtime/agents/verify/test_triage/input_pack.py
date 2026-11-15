"""Input pack Test Triage: pytest finding + bounded change slice и OPSX."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.common.prompt_embed import embed_source_window
from agents.state import GraphState
from agents.verify.static_triage.change_slice import build_change_slice
from agents.verify.static_triage.opsx_context import build_bounded_opsx_context

_TEST_TRIAGE_READ_TOOLS = frozenset({"read"})


def build_test_triage_input_pack(
    *,
    repository_path: str,
    item: dict[str, Any],
    state: GraphState | None = None,
) -> dict[str, Any]:
    """Собрать JSON для test triage (та же bounded дисциплина, что static triage)."""
    path = item.get("path")
    line = item.get("line")
    status, slice_text, hunk_present = build_change_slice(
        repository_path=repository_path,
        path=str(path) if path else None,
        line=int(line) if isinstance(line, int) else None,
    )
    opsx_context: dict[str, Any] = {}
    context_manifest: list[str] = []
    if state is not None:
        opsx_context, context_manifest = build_bounded_opsx_context(state, item=item)

    pytest_failure_excerpt = item.get("pytest_failure_excerpt")
    if not isinstance(pytest_failure_excerpt, str) or not pytest_failure_excerpt.strip():
        pytest_failure_excerpt = None
    source_window = None
    if path and isinstance(path, str):
        source_window = embed_source_window(
            Path(repository_path) / path.replace("\\", "/"),
            int(line) if isinstance(line, int) else None,
        )

    stall_evidence = item.get("pytest_stall_evidence")
    if not isinstance(stall_evidence, dict):
        stall_evidence = None

    return {
        "finding": {
            "finding_id": item.get("finding_id"),
            "observation": item.get("observation"),
            "source_tool": item.get("source_tool"),
            "repair_workflow": item.get("repair_workflow"),
            "path": path,
            "line": line,
        },
        "pytest_failure_excerpt": pytest_failure_excerpt,
        "duration_stall_evidence": stall_evidence,
        "read_tools": sorted(_TEST_TRIAGE_READ_TOOLS),
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
