"""Промпт apply-hop verify subgraph: OPSX contract_prompts, без slash-команд."""

from __future__ import annotations

import json
from typing import Any

from agents.agent_visible_text import sanitize_mapping_for_agent_prompt
from agents.implementation.prompt.opsx_prompt_fragments import verify_apply_hop_authority


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    scenario_coverage_answer: dict[str, Any] | None = None,
    archive_verify_answer: dict[str, Any] | None = None,
    gap_source: str = "impl_verify",
    gaps_for_prompt: dict[str, Any] | None = None,
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None = None,
) -> str:
    """Собрать SDK prompt для ``ApplyAgent`` (authority только из OPSX fragments)."""
    active_gaps = gaps_for_prompt if isinstance(gaps_for_prompt, dict) else impl_verify_answer
    gaps_json = json.dumps(
        sanitize_mapping_for_agent_prompt(active_gaps),
        ensure_ascii=False,
        indent=2,
    )
    impl_json = json.dumps(
        sanitize_mapping_for_agent_prompt(impl_verify_answer),
        ensure_ascii=False,
        indent=2,
    )
    scenario_json = json.dumps(
        sanitize_mapping_for_agent_prompt(scenario_coverage_answer or {}),
        ensure_ascii=False,
        indent=2,
    )
    archive_json = json.dumps(
        sanitize_mapping_for_agent_prompt(archive_verify_answer or {}),
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
    authority = verify_apply_hop_authority()

    if gap_source == "archive_verify":
        gap_origin_line = "PHASE_ARCHIVE verify (❌ NO_GO — not archive-ready)"
    elif gap_source == "scenario_coverage_verify":
        gap_origin_line = "scenario coverage audit (SCENARIO_TEST_COVERAGE_FAIL)"
    else:
        gap_origin_line = "PHASE_IMPL verify (❌ IMPL_GAPS)"

    return f"""You are the OPSX verify-side apply executor invoked after {gap_origin_line}.

Your job in THIS hop: close gaps called out by the verify hop above — minimal
focused edits and task checkboxes only. Do not run archive verify, static-check FULL as
DoD substitute, or claim archive-ready.

Repository (workspace): {repository_path}
Change id: {change_id}
Verify gap source: {gap_source}

Follow the embedded apply rules below, only within this hop's scope.
For scenario-only recovery, do not re-run the ordinary tasks.md implementation
loop or tick frozen planning tasks. Prefer the ACTIVE GAPS JSON.

ACTIVE GAPS (primary input for this hop):
{gaps_json}

IMPL_VERIFY ANSWER (full PHASE_IMPL payload when present):
{impl_json}

SCENARIO COVERAGE ANSWER (audit payload when present):
{scenario_json}

ARCHIVE VERIFY ANSWER (PHASE_ARCHIVE payload when gap source is archive_verify):
{archive_json}

Planning artifacts bundle:
{artifacts_json}

Apply machine context (split-apply classifier; advisory only):
{machine_json}

Hard rules for this graph hop:
- Mutate product/tests only as needed to close listed gaps; keep scope minimal.
- Return exactly one JSON object matching the schema (no markdown fence).
- Populate ``files_changed`` with repo-relative paths you actually edited.
- ``status=NO_CHANGE`` only when no file edits were required AND gaps remain.
- ``status=BLOCKED`` when Owner pause / ADL NO_GO / missing artifact blocks work.
- Do not git commit or push (commit hop follows this node).

--- BEGIN VERIFY APPLY HOP AUTHORITY ---
{authority}
--- END VERIFY APPLY HOP AUTHORITY ---
"""