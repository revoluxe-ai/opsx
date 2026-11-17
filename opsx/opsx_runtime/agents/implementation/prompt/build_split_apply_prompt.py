"""Сборка SDK prompt для split-apply hop без привязки к slash-командам."""

from __future__ import annotations

import json
from typing import Any

from agents.agent_visible_text import sanitize_mapping_for_agent_prompt
from agents.common.contract_prompts import prepend_contracts
from agents.implementation.apply_scaffold.scaffold_hop_visible_text import (
    sanitize_scaffold_hop_visible_text,
)
from agents.implementation.prompt.stage_authority import stage_authority, stage_label
from agents.implementation.prompt.slice_input_pack import build_stage_slice_embed
from pathlib import Path
from agents.implementation.prompt.slice_receipt_hop_tool import slice_receipt_hop_tool_block
from workflows.implementation.gate_error_text import format_gate_error_line
from agents.implementation.prompt.stage_receipt_preservation import (
    STAGE_RECEIPT_PRESERVATION_AUTHORITY,
    green_design_redo_block,
    mid_slice_design_ban_block,
    preservation_dispatch_block,
    slice_receipt_validation_dispatch_block,
)

_SLICE_RECEIPT_TOOL_STAGES = frozenset(
    {"apply_scaffold", "apply_red", "apply_implement", "apply_green"},
)

_AGENT_NAMES = {
    "apply_design": "ApplyDesignAgent",
    "apply_design_review": "ApplyDesignReviewAgent",
    "apply_scaffold": "ApplyScaffoldAgent",
    "apply_red": "ApplyRedAgent",
    "apply_implement": "ApplyImplementAgent",
    "apply_green": "ApplyGreenAgent",
}


def build_split_apply_prompt(
    *,
    stage_key: str,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None,
) -> str:
    """Единый каркас prompt: встроенный stage authority + контекст change.

    JSON-схему ответа сюда не передавать: её дописывает ``ABCAgent`` из
    ``payload_model``, без имени Python-класса.
    """
    label = stage_label(stage_key)
    agent_name = _AGENT_NAMES[stage_key]
    authority = stage_authority(stage_key)
    repo = Path(repository_path)
    slice_id = str((impl_verify_answer or {}).get("slice_id") or "").strip() or None
    slice_embed = build_stage_slice_embed(
        stage_key=stage_key,
        change_id=change_id,
        slice_id=slice_id,
        repo_root=repo,
    )
    slice_embed_json = json.dumps(slice_embed, ensure_ascii=False, indent=2)
    gaps_json = json.dumps(
        sanitize_mapping_for_agent_prompt(impl_verify_answer),
        ensure_ascii=False,
        indent=2,
    )
    artifacts_json = json.dumps(
        sanitize_mapping_for_agent_prompt(artifact_bundle),
        ensure_ascii=False,
        indent=2,
    )
    machine_json = json.dumps(
        sanitize_mapping_for_agent_prompt(machine_context or {}),
        ensure_ascii=False,
        indent=2,
    )
    dispatched_slice = impl_verify_answer.get("slice_id") if impl_verify_answer else None
    dispatch_block = ""
    if dispatched_slice:
        dispatch_block = (
            f"\nDispatched slice for THIS hop only (authoritative; "
            f"do not run selectNextEligibleSlice or pick another slice): "
            f"{dispatched_slice}\n"
        )
    receipt_val_early = (impl_verify_answer or {}).get("slice_receipt_validation")
    reject_code = str((impl_verify_answer or {}).get("plan_rejected_code") or "").strip()
    if reject_code and not isinstance(receipt_val_early, dict):
        dispatch_block += (
            f"\nPrevious APPLY_COMPLETE did not move the plan. "
            f"Gate code: {reject_code}. "
            f"Stay on this dispatched stage until its validator passes. "
            f"Do not return status=APPLY_COMPLETE while that gate still fails.\n"
        )
    if reject_code and isinstance(receipt_val_early, dict):
        dispatch_block += (
            f"\nPrior machine gate code: {reject_code}. Fix within this stage's write "
            "boundary; do not return status=APPLY_COMPLETE until slice work and stage "
            "tools satisfy the authority.\n"
        )
    machine_errors = (impl_verify_answer or {}).get("machine_gate_errors")
    if isinstance(machine_errors, list) and machine_errors:
        lines: list[str] = []
        for entry in machine_errors:
            if isinstance(entry, dict):
                shown = dict(entry)
                if stage_key == "apply_scaffold":
                    shown["message"] = sanitize_scaffold_hop_visible_text(
                        str(entry.get("message") or "")
                    )
                lines.append(f"- {format_gate_error_line(shown)}")
        if lines:
            dispatch_block += (
                "\nMachine validator errors from the prior hop (fix within stage "
                "write boundary; do not edit frozen tests from IMPLEMENT/GREEN):\n"
                + "\n".join(lines)
                + "\n"
            )
    if stage_key == "apply_red":
        dispatch_block += (
            "\nForbidden in APPLY RED: do not modify `apply-design/APPLY-DESIGN.json`, "
            "production code, or scaffold receipts. Incomplete design bindings → "
            "status=ESCALATE_ARBITER with arbiter_request, not silent DESIGN edits.\n"
        )
    receipt_val = (impl_verify_answer or {}).get("slice_receipt_validation")
    if isinstance(receipt_val, dict):
        dispatch_block += slice_receipt_validation_dispatch_block(receipt_val)
    else:
        operative_raw = (impl_verify_answer or {}).get("operative_receipt")
        if isinstance(operative_raw, dict):
            dispatch_block += preservation_dispatch_block(operative_raw)
    dispatch_block += mid_slice_design_ban_block(stage_key)
    dispatch_block += green_design_redo_block(stage_key)
    if stage_key in _SLICE_RECEIPT_TOOL_STAGES:
        dispatch_block += f"\n{slice_receipt_hop_tool_block()}\n"
    arbiter_directive = (impl_verify_answer or {}).get("arbiter_directive")
    if isinstance(arbiter_directive, str) and arbiter_directive.strip():
        dispatch_block += (
            "\n## Arbiter directive (authoritative for this hop)\n"
            f"{arbiter_directive.strip()}\n"
        )

    body = f"""You are the OPSX {label} stage runtime agent ({agent_name}).

Your job in THIS hop: execute the {label} stage per the embedded stage authority below.
Inputs come from LangGraph state (`change_id`, `repository_path`) — not from Owner slash commands.
Do not substitute monolithic legacy apply behavior.

Repository (workspace): {repository_path}
Change id: {change_id}
{dispatch_block}
Prior hop context (verify answer / routing hints when present):
{gaps_json}

Stage/slice inputs (embedded; the same files under ``openspec/changes/<id>/apply-*`` are readable):
{slice_embed_json}

Planning artifacts bundle:
{artifacts_json}

Apply machine context (classifier; advisory only — slice order/stage come from graph):
{machine_json}

Hard rules for this graph hop:
- Do not grep/glob workspace root or hunt receipts; use embedded inputs only.
- When `work_paths` appear in slice receipt gate context, open those paths with
  `read` (or bounded `grep` on a single file) — never `glob` from REPO_ROOT.
- Honor the write boundary and stage bans in the stage authority below.
{STAGE_RECEIPT_PRESERVATION_AUTHORITY}
- Return exactly one JSON object matching the schema (no markdown fence).
- Populate ``files_changed`` with repo-relative paths you actually edited.
- ``status=NO_CHANGE`` only when no file edits were required AND work remains blocked or skipped.
- ``status=BLOCKED`` when Owner pause / ADL NO_GO / missing artifact blocks work.
- ``status=ESCALATE_ARBITER`` when the stage write boundary forbids the honest fix
  (e.g. RED cannot edit APPLY-DESIGN): set ``arbiter_request`` with reason,
  blocker_code, evidence; do NOT use BLOCKED for auto-routable cross-hop gaps.
- Do not git commit or push unless the stage authority explicitly requires it in this stage.
- ``status`` is the only routing signal. In the slice loop, LangGraph advances
  scaffold → red → implement → green for the **dispatched slice** (no plan-node
  dispatch). Do not name a next slash command, next stage, or handoff prompt.
  Fields ``next_command``, ``local_hop_copy_block``, and ``next_action`` are not
  in the schema.

--- BEGIN {label} STAGE AUTHORITY ---
{authority}
--- END {label} STAGE AUTHORITY ---
"""
    return prepend_contracts(body, group="implementation")
