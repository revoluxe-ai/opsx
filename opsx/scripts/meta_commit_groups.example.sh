#!/usr/bin/env bash
# =============================================================================
# meta_commit_groups.example.sh
# =============================================================================
# Назначение: эталон разбиения на 11 групп (прогон 2026-11-02, SHAs f6334cc8…645e96db).
#   Copy-paste нужные commit_group в meta_commit_groups.sh ACTIVE блок.
# Не запускать целиком повторно без правки — пути могут быть уже закоммичены.
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=meta_commit_batch.sh
source "${SCRIPT_DIR}/meta_commit_batch.sh"
export META_COMMIT_REPO_ROOT="${META_COMMIT_REPO_ROOT:-$(meta_commit_batch__repo_root)}"
meta_commit_batch__assert_preflight "$META_COMMIT_REPO_ROOT"

commit_group "docs(commands): align apply lane command contracts (согласование opsx-apply команд)" \
  .cursor/commands/opsx-apply-design.md \
  .cursor/commands/opsx-apply-green.md \
  .cursor/commands/opsx-apply-implement.md \
  .cursor/commands/opsx-apply-red.md \
  .cursor/commands/opsx-apply-scaffold.md \
  .cursor/pipeline/bug-team/workers/CLASSIFIER.md

commit_group "chore(opsx-process): extend sources allowlist (расширение sources.yaml)" \
  opsx/sources.yaml

commit_group "refactor(opsx-process): diagnostic verify agent and input pack (диагностический verify-agent)" \
  opsx/opsx_runtime/agents/verify/diagnostic/agent.py \
  opsx/opsx_runtime/agents/verify/diagnostic/input_pack.py \
  opsx/opsx_runtime/agents/verify/diagnostic/enrichment.py \
  opsx/opsx_runtime/agents/verify/diagnostic/schemas.py \
  opsx/opsx_runtime/agents/verify/diagnostic/prompt/get_prompt.py \
  opsx/opsx_runtime/agents/verify/diagnostic/tests/test_agent.py \
  opsx/opsx_runtime/agents/verify/diagnostic/tests/test_input_pack.py

commit_group "fix(opsx-process): checkpoint resume workflow (расширение checkpoint resume)" \
  opsx/opsx_runtime/workflows/checkpoint_resume.py \
  opsx/opsx_runtime/workflows/repair/tests/test_checkpoint_resume.py

commit_group "refactor(opsx-process): verify graph cycle gates and logging (цикл verify и логирование)" \
  opsx/opsx_runtime/workflows/verify/apply_commit_node.py \
  opsx/opsx_runtime/workflows/verify/conftest.py \
  opsx/opsx_runtime/workflows/verify/cycle_checks.py \
  opsx/opsx_runtime/workflows/verify/cycle_gate.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/commit_node.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/logic_repair.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/repair_state.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/routing.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/subgraph.py \
  opsx/opsx_runtime/workflows/verify/dispatch_cycle/tests/test_repair_unit_dispatch.py \
  opsx/opsx_runtime/workflows/verify/gate_repair/tests/test_subgraph.py \
  opsx/opsx_runtime/workflows/verify/graph.py \
  opsx/opsx_runtime/workflows/verify/green_loop.py \
  opsx/opsx_runtime/workflows/verify/impl_graph.py \
  opsx/opsx_runtime/workflows/verify/outer_loop.py \
  opsx/opsx_runtime/workflows/verify/routing_log.py \
  opsx/opsx_runtime/workflows/verify/run.py \
  opsx/opsx_runtime/workflows/verify/tests/stub_adapters.py \
  opsx/opsx_runtime/workflows/verify/tests/test_green_loop.py \
  opsx/opsx_runtime/workflows/verify/tests/test_impl_graph.py \
  opsx/opsx_runtime/workflows/workflow_logging.py

commit_group "chore(opsx-process): owner-named repair intake (named bug repair)" \
  opsx/opsx_runtime/ui/named_bug_repair/intake.py \
  opsx/opsx_runtime/ui/named_bug_repair/stream_log.py

commit_group "refactor(opsx-process): commit agent and apply implementation hops (commit-agent и apply hops)" \
  opsx/opsx_runtime/agents/common/commit_agent.py \
  opsx/opsx_runtime/agents/common/commit_message \
  opsx/opsx_runtime/agents/implementation/apply/agent.py \
  opsx/opsx_runtime/agents/implementation/apply_design \
  opsx/opsx_runtime/agents/implementation/apply_green \
  opsx/opsx_runtime/agents/implementation/apply_implement \
  opsx/opsx_runtime/agents/implementation/apply_red \
  opsx/opsx_runtime/agents/implementation/apply_scaffold

commit_group "refactor(opsx-process): verify dispatcher and impl-verify agents (dispatcher и impl-verify)" \
  opsx/opsx_runtime/agents/verify/dispatcher/__init__.py \
  opsx/opsx_runtime/agents/verify/dispatcher/agent.py \
  opsx/opsx_runtime/agents/verify/dispatcher/queue.py \
  opsx/opsx_runtime/agents/verify/dispatcher/items.py \
  opsx/opsx_runtime/agents/verify/dispatcher/tests/test_queue.py \
  opsx/opsx_runtime/agents/verify/dispatch_unit \
  opsx/opsx_runtime/agents/verify/impl_verify/agent.py \
  opsx/opsx_runtime/agents/verify/impl_verify/prompt/get_prompt.py \
  opsx/opsx_runtime/agents/verify/impl_verify/report.py \
  opsx/opsx_runtime/agents/verify/impl_verify/schemas.py \
  opsx/opsx_runtime/agents/verify/impl_verify/tests/test_agent_short_circuit.py \
  opsx/opsx_runtime/agents/verify/impl_verify/tests/test_impl_verify_helpers.py \
  opsx/opsx_runtime/agents/verify/scenario_coverage_verify \
  opsx/opsx_runtime/run_verify_impl.py \
  opsx/opsx_runtime/run_verify_deterministic_smoke.py

commit_group "refactor(opsx-process): verify agent suite and adapters (набор verify-агентов и адаптеры)" \
  opsx/opsx_runtime/agents/abc_agent.py \
  opsx/opsx_runtime/agents/adapters/abc_adapter.py \
  opsx/opsx_runtime/agents/adapters/cursor_sdk_adapter.py \
  opsx/opsx_runtime/agents/adapters/cursor_sdk_stream.py \
  opsx/opsx_runtime/agents/adapters/tests/test_adapter_resume_agent_id.py \
  opsx/opsx_runtime/agents/state.py \
  opsx/opsx_runtime/agents/tests/test_agent_json_object.py \
  opsx/opsx_runtime/agents/tests/test_validated_json_hop.py \
  opsx/opsx_runtime/agents/verify/bugbot/agent.py \
  opsx/opsx_runtime/agents/verify/graph_validation.py \
  opsx/opsx_runtime/agents/verify/group_error/agent.py \
  opsx/opsx_runtime/agents/verify/group_error/tests/test_agent.py \
  opsx/opsx_runtime/agents/verify/ok_probe/agent.py \
  opsx/opsx_runtime/agents/verify/project_bug_hunt/agent.py \
  opsx/opsx_runtime/agents/verify/repair_order/agent.py \
  opsx/opsx_runtime/agents/verify/repair_order/sort_units.py \
  opsx/opsx_runtime/agents/verify/repair_order/tests/test_agent.py \
  opsx/opsx_runtime/agents/verify/security_review/agent.py \
  opsx/opsx_runtime/agents/verify/static_triage/agent.py \
  opsx/opsx_runtime/agents/verify/static_triage/prompt/unit_repair_triage.py \
  opsx/opsx_runtime/agents/verify/test_repair/agent.py \
  opsx/opsx_runtime/agents/verify/test_triage/agent.py \
  opsx/opsx_runtime/agents/verify/typing_repair/agent.py \
  opsx/opsx_runtime/agents/repair/nodes_repair.py

commit_group "refactor(opsx-process): repair workflow and main graph entrypoints (repair workflow и main)" \
  opsx/opsx_runtime/workflows/repair/control.py \
  opsx/opsx_runtime/workflows/repair/cursor_adapter.py \
  opsx/opsx_runtime/workflows/repair/graph.py \
  opsx/opsx_runtime/workflows/repair/primitive.py \
  opsx/opsx_runtime/workflows/repair/schemas/domain.py \
  opsx/opsx_runtime/workflows/repair/tests/fakes.py \
  opsx/opsx_runtime/workflows/repair/tests/test_control.py \
  opsx/opsx_runtime/workflows/repair/tests/test_graph.py \
  opsx/opsx_runtime/workflows/repair/tests/test_schemas.py \
  opsx/opsx_runtime/workflows/main.py \
  opsx/opsx_runtime/workflows/main_graph.py \
  opsx/opsx_runtime/workflows/repo_paths.py \
  opsx/opsx_runtime/run.py \
  opsx/opsx_runtime/run_dialogs.py \
  opsx/opsx_runtime/reconcile_one_node.py \
  opsx/opsx_runtime/tests/test_real_runner.py \
  opsx/opsx_runtime/tests/test_single_node.py \
  opsx/opsx_runtime/openspec

commit_group "chore(opsx-process): opsx helper scripts and pack utility (вспомогательные opsx scripts)" \
  opsx/_pack_agent_dialogs.py \
  opsx/scripts

echo "example groups finished"
