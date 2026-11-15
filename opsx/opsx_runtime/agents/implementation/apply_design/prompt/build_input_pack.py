"""Сборка InputPack для apply-design hop (точные source_id, без угадывания)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agents.capabilities.manifest import AgentCapabilityManifest
from agents.implementation.apply_design.capability import (
    PLANNING_SOURCE_ALIASES,
    file_digest_bytes,
    planning_source_path,
)


async def _revision_for_path(path: Path | None) -> str | None:
    """Digest файла или ``None``, если файла нет."""
    if path is None or not path.is_file():
        return None
    data = await asyncio.to_thread(path.read_bytes)
    return file_digest_bytes(data)


async def build_apply_design_input_pack(
    manifest: AgentCapabilityManifest,
) -> dict[str, Any]:
    """Runtime InputPack: обязательные planning source_id и snapshot-токены."""
    repo = manifest.repo_root.resolve()
    change_id = manifest.change_id
    planning_entries: list[dict[str, Any]] = []
    for alias in sorted(PLANNING_SOURCE_ALIASES):
        path = planning_source_path(repo, alias, change_id)
        digest = await _revision_for_path(path)
        planning_entries.append(
            {
                "source_id": alias,
                "present": digest is not None,
                "revision_digest": digest,
            }
        )
    spec_sources = sorted(
        sid for sid in manifest.readable_sources if sid.startswith("changes:") and "/specs/" in sid
    )
    submission_block: dict[str, Any] = {
        "revision_digest": await manifest.submission_digest_async(),
    }
    if manifest.design_submission is not None:
        submission_block["base_digest_at_hop_start"] = manifest.design_submission.base_digest
    return {
        "change_id": change_id,
        "mandatory_planning_sources": planning_entries,
        "readable_planning_alias_ids": sorted(
            e["source_id"] for e in planning_entries if e["present"]
        ),
        "spec_source_ids": spec_sources,
        "prior_design_source_ids": sorted(manifest.prior_designs.keys()),
        "apply_design_submission": submission_block,
        "planning_complete": manifest.planning_complete,
        "planning_gaps": list(manifest.planning_gaps),
    }


def format_input_pack_prompt_block(pack: dict[str, Any]) -> str:
    """Текстовый блок prompt агента: ids из runtime, не вычислять SHA вручную."""
    body = json.dumps(pack, ensure_ascii=False, indent=2)
    return f"""
## Apply-design InputPack (runtime manifest; authoritative source ids)
Use these exact ``source_id`` values with ``read_source`` — do not invent aliases
(``planning:scenario_plan``, ``hop:embedded:*``, and similar are invalid).
Runtime computed revision digests below; pass ``expected_revision`` on cursor > 0
when paging large bodies. Do not compute SHA-256 yourself.

{body}
"""
