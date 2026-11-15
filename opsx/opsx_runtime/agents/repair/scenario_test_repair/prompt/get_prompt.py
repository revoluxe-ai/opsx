"""ScenarioTestRepair prompt using the exact context assembled for Apply today."""
from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Any

from agents.implementation.apply.prompt.get_prompt import read_apply_command_contract


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    scenario_coverage_answer: dict[str, Any],
    gaps_for_prompt: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any],
    repair_binding: dict[str, Any] | None = None,
) -> str:
    """Same six sources as Verify's Apply prompt, narrower write authority.

    No synthetic scenario examples: the incoming coverage answer and repo
    artifacts are passed through unchanged.
    """
    repo = Path(repository_path).resolve()
    if not (resource_path(repo, ".cursor/commands/opsx-apply.md")).is_file():
        raise ValueError("REPAIR_AUTHORITY_MISSING: .cursor/commands/opsx-apply.md")
    command = await read_apply_command_contract(repo_root=repo)

    def j(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    return f"""You are ScenarioTestRepairAgent, a narrow TEST-ONLY repair executor.

Repository (workspace): {repository_path}
Change id: {change_id}
Verify gap source: scenario_coverage_verify

The fields below are the EXACT current inputs of the existing Apply hop:
ACTIVE GAPS (primary input):
{j(gaps_for_prompt)}

IMPL_VERIFY ANSWER:
{j(impl_verify_answer)}

SCENARIO COVERAGE ANSWER:
{j(scenario_coverage_answer)}

Planning artifacts bundle:
{j(artifact_bundle)}

Apply machine context:
{j(machine_context)}

FROZEN REPAIR BINDINGS (machine; edit ONLY allowed_repair_paths):
{j(repair_binding or {})}

Authority and acceptance:
- Existing /opsx-apply contract is provided for contextual constraints, not
  permission to enter unrelated implementation/split-APPLY stages.
- Only tests/** and test helpers/fixtures inside tests/** are writable.
  Production source, frozen plan, specs, tasks.md, trace matrix, commands,
  runtime, config, dependencies, branches, commits and PRs are READ ONLY.
- Do not repair a real production behavior defect by weakening a test.
  If a gap cannot be fixed under test-only authority, BLOCKED.
- Read ALL incoming gaps and inspect exact frozen WHEN/THEN/AND, mapped
  test bodies, their called helpers, fixtures and production seam.
REPAIR PLAYBOOK (model-only; no deterministic copy of peer files):
1. Open ONLY frozen_mapped_test_path / the function in frozen_mapped_test_node.
2. Use reference_test_nodes as READ-ONLY templates: borrow assertion patterns
   (verification, inbox/intent, reason codes), never write peer file paths.
3. If the mapped module holds multiple scenarios, strengthen ONLY the one test
   function for the current scenario_id; do not overwrite sibling SCENARIO_IDs.
4. Presence of process_pending or a single coarse assert does NOT close a GAP;
   mirror peer WHEN/THEN proof on the mapped seam under allowed_repair_paths.
5. Prefer integration peer bodies for assertion ideas; apply edits only on mapped.
- CANDIDATE_PENDING_VERIFY requires edits under ``allowed_repair_paths`` in
  FROZEN REPAIR BINDINGS for every addressed_gap_id. Integration/unit-only
  edits without mapped touch are forbidden.
- ``unverified_tests`` MUST list only ``frozen_mapped_test_node`` values from
  bindings, never reference_test_nodes peers.
- Before editing, establish concrete causal reachability. A webhook-only
  test does not prove processor/intent behavior; a non-recording SQL helper
  does not prove a negative PostgreSQL-write claim. Do not invent evidence.
- Repair all authorized independent gaps in one hop, with the smallest
  changed test surface. Never create assertions that only inspect a mock
  that cannot be reached by the scenario's triggering path.
- Do not invoke shell, pytest, Poetry, or terminal commands. The separate
  test runner owns execution and verification; report unverified_tests instead
  of claiming PASS. Missing safe DB is not permission to run checks yourself.
- When the mapped test uses disposable PostgreSQL fixtures or real PG I/O,
  add `@pytest.mark.workflow_deferred` on that test function (authoring
  contract: written, marked, not executed inside OPSX verify workflow).
- Every reported changed path must match actual tests/** modifications.
  An unexpected write makes this hop BLOCKED; never conceal it.
- addressed_gap_ids and remaining_gap_ids partition EXACT incoming scenario
  ids. If all have test-only candidate changes, return CANDIDATE_PENDING_VERIFY;
  this is NOT PASS and must be independently re-audited.
- PARTIAL requires remaining_gap_ids; BLOCKED/NO_CHANGE must include an
  exact recovery_contract. Never suggest restarting the same unresolved loop.
- Tool/check claims in checks_reported are advisory; they are NOT machine
  evidence. Neither passing a weak test nor editing a trace label constitutes
  supported coverage.
- Do not git commit/push. No archive, no implicit approval or scope expansion.
- Output one JSON object matching the schema.

--- BEGIN /opsx-apply COMMAND CONTRACT (REFERENCE; TEST-ONLY AUTHORITY ABOVE WINS) ---
{command}
--- END /opsx-apply COMMAND CONTRACT ---
"""
