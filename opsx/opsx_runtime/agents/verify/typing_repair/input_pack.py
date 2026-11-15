"""Input pack Typing Repair: finding + triage + bounded OPSX."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.common.prompt_embed import embed_source_window
from agents.state import GraphState
from agents.verify.static_triage.change_slice import build_change_slice
from agents.verify.static_triage.opsx_context import build_bounded_opsx_context


def build_typing_repair_input_pack(
    *,
    repository_path: str,
    item: dict[str, Any],
    state: GraphState,
    triage: dict[str, Any],
) -> dict[str, Any]:
    """Собрать JSON для typing fix (узкий scope, тот же bounded opsx что triage)."""
    path = item.get("path")
    line = item.get("line")
    status, slice_text, hunk_present = build_change_slice(
        repository_path=repository_path,
        path=str(path) if path else None,
        line=int(line) if isinstance(line, int) else None,
    )
    opsx_context, context_manifest = build_bounded_opsx_context(state, item=item)
    source_window = None
    if path and isinstance(path, str):
        source_window = embed_source_window(
            Path(repository_path) / path.replace("\\", "/"),
            int(line) if isinstance(line, int) else None,
        )
    return {
        "finding": item,
        "static_triage": triage,
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
