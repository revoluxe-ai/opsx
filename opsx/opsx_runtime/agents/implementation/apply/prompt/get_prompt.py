"""Промпт apply-hop: контракт ``/opsx-apply`` + контекст gaps из verify (IMPL или scenario coverage)."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
from pathlib import Path
from typing import Any


def _read_command_contract(*, repo_root: Path) -> str:
    """Прочитать authoritative apply contract из ``.cursor/commands/opsx-apply.md``."""
    path = resource_path(repo_root, ".cursor", "commands", "opsx-apply.md")
    if not path.is_file():
        return (
            "Authority missing: `.cursor/commands/opsx-apply.md` not found. "
            "Follow split-apply + legacy tasks loop from VERIFY_PHASES IMPL_GAPS."
        )
    return path.read_text(encoding="utf-8")


async def read_apply_command_contract(*, repo_root: Path) -> str:
    """Асинхронно прочитать оригинал команды ``/opsx-apply`` для вставки в SDK prompt.

    Читает ``.cursor/commands/opsx-apply.md`` в thread pool (не блокирует event loop).
    """
    return await asyncio.to_thread(_read_command_contract, repo_root=repo_root)


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
    """Собрать SDK prompt для ``ApplyAgent`` (контракт команды читается асинхронно)."""
    repo = Path(repository_path).resolve()
    active_gaps = gaps_for_prompt if isinstance(gaps_for_prompt, dict) else impl_verify_answer
    gaps_json = json.dumps(active_gaps, ensure_ascii=False, indent=2)
    impl_json = json.dumps(impl_verify_answer, ensure_ascii=False, indent=2)
    scenario_json = json.dumps(scenario_coverage_answer or {}, ensure_ascii=False, indent=2)
    archive_json = json.dumps(archive_verify_answer or {}, ensure_ascii=False, indent=2)
    artifacts_json = json.dumps(artifact_bundle, ensure_ascii=False, indent=2)
    machine_json = json.dumps(machine_context or {}, ensure_ascii=False, indent=2)
    contract = (
        "Graph apply hop (embedded rules — do not read `.cursor/commands/**`):\n"
        "- Close only gaps listed in ACTIVE GAPS JSON; minimal edits.\n"
        "- Use embedded planning bundle; do not run openspec list/status/discovery.\n"
        "- Do not write machine receipts under opsx/receipts.\n"
        "- Return ApplyAgent JSON only; runtime owns routing."
    )

    if gap_source == "archive_verify":
        gap_origin_line = "PHASE_ARCHIVE verify (❌ NO_GO — not archive-ready)"
    elif gap_source == "scenario_coverage_verify":
        gap_origin_line = "scenario coverage audit (SCENARIO_TEST_COVERAGE_FAIL)"
    else:
        gap_origin_line = "PHASE_IMPL verify (❌ IMPL_GAPS)"

    return f"""You are the OPSX apply executor (`/opsx-apply`) invoked from verify after {gap_origin_line}.

Your job in THIS hop: close gaps called out by the verify hop above — minimal
focused edits and task checkboxes only. Do not run archive verify, static-check FULL as
DoD substitute, or claim archive-ready.

Repository (workspace): {repository_path}
Change id: {change_id}
Verify gap source: {gap_source}

Follow the embedded apply rules below, only within this hop's scope.
For scenario-only recovery, do not re-run the ordinary tasks.md implementation
loop or tick frozen planning tasks. Prefer the ACTIVE GAPS JSON and do not treat
a model-suggested ``next_command`` as authority to retry.

ACTIVE GAPS (primary input for this hop):
{gaps_json}

IMPL_VERIFY ANSWER (full PHASE_IMPL payload when present):
{impl_json}

SCENARIO COVERAGE ANSWER (audit payload when present):
{scenario_json}

ARCHIVE VERIFY ANSWER (PHASE_ARCHIVE payload when gap source is archive_verify):
{archive_json}

Archive readiness recovery (apply ONLY if Verify gap source is archive_verify):
- Close gaps from ACTIVE GAPS / gap_details and planning completeness only; minimal edits.
- Do not run ``/opsx-archive``, sync specs to main, or claim archive GO.
- Prefer tasks.md checkboxes and planning artifact fixes when gaps are completeness/coherence.
- Runtime re-enters impl_verify after commit; static-check FULL is not archive DoD.

Planning artifacts bundle:
{artifacts_json}

Apply machine context (split-apply classifier; advisory for apply routing only):
{machine_json}

Scenario-coverage recovery rules (apply ONLY if Verify gap source is scenario_coverage_verify):
- Start by reading the entire ACTIVE GAPS inventory, the relevant frozen plan rows,
  specs, mapped test bodies, and existing helpers. Cluster sibling findings by
  causal defect before editing; do not fix only the first highlighted symptom.
- Treat an audit finding as a hypothesis to substantiate with source evidence.
  If it is a reviewer mistake, or conflicts with an explicit frozen plan
  requirement, do not invent tests, silently relax the plan, or alter
  test_trace_matrix.json merely to satisfy the review. Report the exact
  mismatch as BLOCKED with remaining_gaps and actionable evidence.
- Make a minimal behaviorally meaningful change for each repairable causal gap.
  A new SCENARIO_ID label, renamed test_node_id, import of a contract module,
  duplicated mock test, or blanket 'verification' flag is NOT coverage proof.
  Neither is a green test whose helper asserts weaker behavior than frozen
  WHEN/THEN or whose patch/mock never reaches the triggering production seam.
- For each active scenario, explicitly inspect the exact frozen WHEN/THEN/AND
  and the mapped test's invoked production path. Verify assertions inspect
  required side effects and negative boundaries (SQL writes, external
  sends, booking/dedupe functions), not merely method names or empty stubs.
  Follow assert_* helpers to their implementation: checking an unset
  _last_stmt attribute, for example, is not proof of no PostgreSQL write.
- Do not report APPLY_COMPLETE merely because a focused pytest file passed.
  A strong closure needs meaningful scenario assertions and compatible
  shared-fixture/cohort execution; an independent source-bound re-audit is
  the only authority that can confirm coverage.
  Only remap a scenario to a test if the called path and assertions establish
  that scenario's specified behavior.
- Use HTTP contract stubs only when actual scenario execution crosses an HTTP
  boundary. Respect explicitly frozen verification requirements; do not weaken
  them by interpretation. Unresolvable applicability conflicts mean BLOCKED.
- Execute focused checks for modified tests; run PG integration on a proven
  disposable TEST_DATABASE_URL only. Never run destructive PG checks against
  preview/production or mask nonzero exit codes with shell piping.
  If required checks cannot run, say exactly which tests remain unverified;
  do not mark all gaps closed or fabricate a green result.
- APPLY_COMPLETE means every ACTIVE gap has a supported closure with actual
  evidence, not merely changed files. APPLY_PARTIAL means some findings were
  corrected but remaining_gaps is nonempty. BLOCKED means no safe, authorized
  next correction can be proved (including contract conflict or required
  verification environment unavailable). NO_CHANGE never means PASS.
- Do not edit the frozen plan, tasks.md, or product code just to turn a
  scenario-coverage FAIL into PASS. Product code is in scope only when a
  concrete specified behavior defect is independently established and the
  authoritative apply contract permits that change.
- If the same causal gap remains unresolved or a required check regresses,
  stop and report it rather than prescribing another identical Apply/Verify
  round. The next_command field is informational: do not suggest automatic
  retry when BLOCKED or unresolved; use an explicit STOP/OWNER_ATTENTION
  description instead. The runtime owns actual routing.

One-pass closure protocol (mandatory for scenario_coverage_verify):
1. Build a COMPLETE closure ledger before the first edit: take every incoming gap,
   group related causes, identify all affected scenarios, existing test paths, source
   seams, frozen constraints, and explicit acceptance assertions. Also inspect
   adjacent scenarios sharing those seams; do not discover them only after edits.
2. Execute ALL authorized, repairable causal groups in this same Apply hop, not
   merely the first group or the easiest two. A full hop may involve multiple
   focused changes and checks, but only one final structured response. Do not
   prematurely return APPLY_PARTIAL because some gaps remain unexamined.
3. Before returning, revisit the COMPLETE incoming gap inventory and affected
   siblings, comparing current files with spec behavior and concrete assertions.
   Output ``addressed_gap_ids`` containing every scenario_id actually resolved;
   unresolved items go into ``remaining_gaps`` with exact cause and path.
   An updated matrix, SCENARIO_ID label or successful static matrix test is NOT
   behavioral proof.
4. APPLY_COMPLETE requires full supported closure of every active gap, no
   outstanding required tests, and no deferred work. Supply ``unverified_tests``
   explicitly; if PostgreSQL/other mandatory checks cannot execute safely, return
   APPLY_PARTIAL or BLOCKED, not COMPLETE. Never claim green from source inspection.
5. Do not silently change frozen plan, validation semantics, test expectations,
   runtime authority or unrelated implementation to obtain coverage. A genuine
   contract conflict is BLOCKED with exact recovery details.
6. If blocked or partial after an exhaustive repair attempt, STOP. Do not ask
   the orchestration loop to rediscover gaps and re-run the same Apply hop.
   Same-execution resume may preserve context, but use fresh file/receipt identities
   and the durable closure ledger; do not trust stale SDK dialogue over repository state.

For APPLY-DESIGN ``image_tasks``: use MCP ``execute_image_task`` (ImageAssetAgent).
Legacy IHDR normalize only — MCP ``visual_cover_asset``, not shell:
- ``inspect``: readable IHDR audit + ``cover_dimensions.json`` presence.
- ``normalize_registry``: letterbox all seven registry PNGs to 1080×1350 and refresh manifest.
- ``normalize_one``: single ``COVER_FILENAMES`` entry under
  ``src/stroy/channels/telegram/invest_yalta/data/``.
- ``generate_illustration``: not configured in apply-hop (adapter reserved;
  use Owner-approved art or ImageAgent for creative work).
- After normalization, include touched ``data/cover_*.png`` and/or manifest in
  ``files_changed``; runtime re-audits bundle before treating APPLY_COMPLETE as closed.

Hard rules for this graph hop:
- Mutate product/tests only as needed to close listed gaps; keep scope minimal.
- Return exactly one JSON object matching the schema (no markdown fence).
- Populate ``files_changed`` with repo-relative paths you actually edited.
- ``status=NO_CHANGE`` only when no file edits were required AND gaps remain.
- ``status=BLOCKED`` when Owner pause / ADL NO_GO / missing artifact blocks work.
- Do not git commit or push (commit hop follows this node).

--- BEGIN /opsx-apply COMMAND CONTRACT ---
{contract}
--- END /opsx-apply COMMAND CONTRACT ---
"""
