"""Агент исправления только сценарных тестов; в Verify-граф V0 не включён.

Поток: проверить authority и source identity -> собрать контекст Apply ->
предложить изменения только tests/** -> сверить реальные изменения ->
вернуть CANDIDATE_PENDING_VERIFY либо BLOCKED.

Модель не выносит PASS: нужен отдельный свежий ScenarioCoverageVerify.
Импорт этого файла не должен требовать адаптер, но live execution
по-прежнему требует авторизованный SDK-адаптер.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import (
    agent_session_patch,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.repair.helpers import MUTATING

# Test-only repair: scoped read + edit; без grep/glob/ls/shell (repo_root_scan).
_SCENARIO_TEST_REPAIR_TOOLS = tuple(
    t for t in MUTATING if t not in ("grep", "glob", "ls", "shell")
)
from agents.repair.scenario_test_repair.mapped_surface import validate_mapped_surface_gates
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    ensure_workflow_deferred_for_scenario,
)
from agents.repair.scenario_test_repair.matrix_rebind import rebind_trace_to_strongest_peer
from agents.repair.scenario_test_repair.prompt.get_prompt import get_prompt
from agents.repair.scenario_test_repair.repair_context import build_repair_binding_context
from agents.repair.scenario_test_repair.safety import (
    RepairSafetyError,
    changed_paths,
    disallowed_paths,
    workspace_snapshot,
)
from agents.repair.scenario_test_repair.schemas import ScenarioTestRepairAnswer
from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot
from agents.verify.scenario_coverage_verify.input_pack import (
    assess_plan_materialization,
    load_scenario_coverage_plan_bundle,
)
from workflows.repo_paths import resolve_opsx_repository_root


def _scenario_repair_stream_label(state: dict[str, Any]) -> str:
    """Сформировать строку лога без импорта stream-модуля адаптера.

    Раньше использовали compose_agent_stream_label из stream shim:
    тот модуль при импорте требует типы провайдера. Для этой роли
    достаточно имени, model.id и необязательного stream_label.
    Эта функция только формирует строку; не открывает сессию и не даёт
    никаких execution permissions. Суффикса round=N у роли нет.
    """
    # Пример: scenario_test_repair model=composer-2.5
    label = "scenario_test_repair"
    model = state.get("model")
    if isinstance(model, dict) and model.get("id"):
        label += f" model={model['id']}"
    scope = state.get("stream_label")
    if isinstance(scope, str) and scope.strip() and "|" not in scope:
        # Пример: run-123|scenario_test_repair model=composer-2.5.
        # Символ | внутри scope запрещён: это разделитель в live-логе.
        return f"{scope.strip()}|{label}"
    return label


def _gap_ids(answer: dict[str, Any]) -> list[str]:
    """Собрать ``scenario_id`` из audit ``gaps`` для repair gap accounting.

    Независимый row-walk иногда пишет несколько gap-строк на один
    ``scenario_id`` (разные facet). Для одной сцены это один repair target —
    dedupe с сохранением порядка. Несколько *разных* scenario_id по-прежнему
    требуют уникальности без повторов.
    """
    gaps = answer.get("gaps")
    if not isinstance(gaps, list):
        return []
    if not gaps or any(not isinstance(g, dict) for g in gaps):
        return []
    ids = [str(g.get("scenario_id") or "").strip() for g in gaps]
    if not all(ids):
        return []
    unique = list(dict.fromkeys(ids))
    if len(unique) == 1:
        return unique
    if len(ids) == len(set(ids)):
        return ids
    return []


def _block_patch(
    reason: str, ids: list[str], *, execution_id: str = "",
    changed: list[str] | None = None,
) -> dict[str, Any]:
    answer = ScenarioTestRepairAnswer(
        status="BLOCKED",
        summary="Scenario test repair could not be proven safe or complete.",
        remaining_gap_ids=ids,
        files_changed=changed or [],
        recovery_contract=reason,
    )
    return {
        "scenario_test_repair_status": "BLOCKED",
        "scenario_test_repair_answer": answer.model_dump(mode="json"),
        "scenario_test_repair_owner_attention": reason,
        "scenario_test_repair_execution_id": execution_id,
        "scenario_test_repair_independent_verified": False,
    }


def validate_input(
    state: dict[str, Any], *, repo_root: Path, change_id: str,
) -> str | None:
    """Fail BEFORE SDK mutation on authority, revision or execution mismatch."""
    model = state.get("model")
    if not isinstance(model, dict) or not str(model.get("id") or "").strip():
        return "REPAIR_MODEL_UNBOUND: explicit SDK model selection required"
    if state.get("scenario_test_repair_authorized") is not True:
        return "REPAIR_NOT_AUTHORIZED: explicit scenario_test_repair_authorized=true required"
    graph_hop = state.get("scenario_test_repair_graph_hop") is True
    if not graph_hop and state.get("isolated_workspace") is not True:
        return "REPAIR_NOT_ISOLATED: disposable isolated workspace attestation required"
    run_uuid = str(state.get("run_uuid") or "").strip()
    if not run_uuid:
        return "REPAIR_EXECUTION_UNBOUND: run_uuid missing"
    if state.get("scenario_coverage_verdict") != "SCENARIO_TEST_COVERAGE_FAIL":
        return "REPAIR_NOT_SCENARIO_FAIL: expected scenario coverage FAIL"
    audit = state.get("scenario_coverage_answer")
    snapshot = state.get("scenario_coverage_source_snapshot")
    if not isinstance(audit, dict) or not isinstance(snapshot, dict) or not _gap_ids(audit):
        return "REPAIR_AUDIT_UNBOUND: fail evidence/source snapshot/gap ids missing"
    if graph_hop:
        if str(state.get("verify_scenario_repair_audit_run_uuid") or "") != run_uuid:
            return (
                "REPAIR_EXECUTION_STALE: scenario coverage audit is not bound to this verify hop"
            )
    elif str(state.get("scenario_coverage_execution_id") or "") != run_uuid:
        return "REPAIR_EXECUTION_STALE: coverage evidence comes from another execution"
    if str(audit.get("verdict") or "") != "SCENARIO_TEST_COVERAGE_FAIL":
        return "REPAIR_AUDIT_INCONSISTENT: verdict does not match state"
    try:
        plan = load_scenario_coverage_plan_bundle(change_id, repo_root=repo_root)
        plan_problem = assess_plan_materialization(plan)
        if plan_problem:
            return f"REPAIR_PLAN_UNAVAILABLE: {plan_problem}"
        receipt_id = str((plan.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or "")
        if not receipt_id or receipt_id != str(audit.get("plan_evidence_id") or ""):
            return "REPAIR_PLAN_STALE: plan receipt identity differs from audit"
        current = input_snapshot(repo_root, change_id)["source_digest"]
    except (OSError, ValueError, KeyError) as exc:
        return f"REPAIR_INPUT_UNAVAILABLE: {type(exc).__name__}: {exc}"
    if current != snapshot.get("source_digest") or current != audit.get("source_digest"):
        return "REPAIR_SOURCE_STALE: source differs from audited revision"
    return None


def validate_candidate(
    answer: ScenarioTestRepairAnswer,
    expected_ids: list[str],
    *,
    actual_changed: list[str],
    repo: Path | None = None,
    change_id: str | None = None,
) -> str | None:
    """Check causal inventory accounting and filesystem receipts, not LLM claims."""
    expected = set(expected_ids)
    addressed = set(answer.addressed_gap_ids)
    remaining = set(answer.remaining_gap_ids)
    if addressed | remaining != expected or addressed & remaining:
        return "REPAIR_GAP_ACCOUNTING_INVALID: result does not partition all input gaps"
    if set(answer.files_changed) != set(actual_changed):
        return "REPAIR_FILES_MISMATCH: reported paths do not match filesystem"
    if disallowed_paths(actual_changed):
        return "REPAIR_SCOPE_VIOLATION: files changed outside tests/**"
    if answer.status == "CANDIDATE_PENDING_VERIFY" and (
        addressed != expected or not actual_changed
    ):
        return "REPAIR_CANDIDATE_UNSUPPORTED: cannot assert candidate without changed tests"
    if answer.status == "NO_CHANGE" and (addressed or actual_changed):
        return "REPAIR_NO_CHANGE_CONFLICT: no-change cannot have addressed gaps or edits"
    if answer.status == "BLOCKED" and addressed:
        return "REPAIR_BLOCKED_WITH_CLAIMED_CLOSURES: fail closed"
    if (
        repo is not None
        and change_id
        and answer.status == "CANDIDATE_PENDING_VERIFY"
        and answer.addressed_gap_ids
    ):
        surface_error = validate_mapped_surface_gates(
            answer.status,
            addressed_ids=answer.addressed_gap_ids,
            actual_changed=actual_changed,
            repo=repo,
            change_id=change_id,
        )
        if surface_error:
            return surface_error
    return None


class ScenarioTestRepairAgent(ABCAgent):
    """Explicitly invoked test-only repair; not a substitution for Apply."""

    def _preflight_problem(
        self, state: dict[str, Any], *, repo_root: Path, change_id: str,
    ) -> str | None:
        """Default production authority/freshness gate. Replay-only test
        subclasses can replace it without mutating this production contract.
        """
        return validate_input(state, repo_root=repo_root, change_id=change_id)

    async def run(
        self, state: GraphState, *, repository_path: str | Path | None = None,
    ) -> dict[str, Any]:
        execution_state: dict[str, Any] = dict(state)
        requested = repository_path if repository_path is not None else state.get("repository_path")
        if not requested:
            return _block_patch("REPAIR_REPOSITORY_UNBOUND: repository_path missing", [])
        try:
            repo = resolve_opsx_repository_root(Path(str(requested)).resolve())
        except (OSError, ValueError) as exc:
            return _block_patch(
                f"REPAIR_REPOSITORY_INVALID: {type(exc).__name__}: {exc}", [],
            )
        if not repo.is_dir():
            return _block_patch(f"REPAIR_REPOSITORY_MISSING: {repo}", [])

        # The target repository may be supplied as a parameter. Never silently
        # substitute another workspace or carry over a different audit identity.
        # Never replay another repository's audit merely because its source
        # digest happens to match: explicit target paths must match the
        # source repo identity present in the incoming GraphState.
        state_repo = state.get("repository_path")
        if state_repo and Path(str(state_repo)).resolve() != repo:
            return _block_patch("REPAIR_REPOSITORY_IDENTITY_MISMATCH: fresh audit required", [])
        execution_state["repository_path"] = str(repo)
        if repository_path is not None:
            execution_state["workspace"] = str(repo)
        elif Path(str(state.get("workspace") or repo)).resolve() != repo:
            return _block_patch("REPAIR_WORKSPACE_MISMATCH: workspace differs from repo", [])

        change_id = str(execution_state.get("change_id") or "").strip()
        audit = execution_state.get("scenario_coverage_answer")
        ids = _gap_ids(audit) if isinstance(audit, dict) else []
        execution_id = str(execution_state.get("run_uuid") or "")
        if not change_id or "/" in change_id or "\\" in change_id or change_id in (".", ".."):
            return _block_patch("REPAIR_CHANGE_ID_INVALID", ids, execution_id=execution_id)

        problem = self._preflight_problem(execution_state, repo_root=repo, change_id=change_id)
        if problem:
            return _block_patch(problem, ids, execution_id=execution_id)

        try:
            before = workspace_snapshot(repo)
            artifacts = load_change_artifact_bundle(change_id, repo_root=repo)
            machine = collect_apply_machine_context(change_id, repo_root=repo)
            impl = execution_state.get("answer")
            impl_answer = impl if isinstance(impl, dict) else {}
            repair_attempts = int(execution_state.get("scenario_test_repair_row_attempts") or 0)
            matrix_rebind: dict[str, Any] | None = None
            if len(ids) == 1 and repair_attempts >= 2:
                matrix_rebind = rebind_trace_to_strongest_peer(repo, change_id, ids[0])
            binding = build_repair_binding_context(repo, change_id, ids)
            prompt = await get_prompt(
                repository_path=str(repo), change_id=change_id,
                impl_verify_answer=impl_answer,
                scenario_coverage_answer=audit,
                gaps_for_prompt=audit,
                artifact_bundle=artifacts, machine_context=machine,
                repair_binding=binding,
            )
        except (OSError, ValueError, RepairSafetyError) as exc:
            return _block_patch(
                f"REPAIR_PREFLIGHT_BLOCKED: {type(exc).__name__}: {exc}",
                ids, execution_id=execution_id,
            )

        sdk_error: str | None = None
        outcome: Any | None = None
        answer: ScenarioTestRepairAnswer | None = None
        try:
            outcome = await self._adapter.run(
                adapter_transport_state(
                    execution_state,
                    stream_log=execution_state.get("apply_stream_log")
                    or execution_state.get("stream_log"),
                    stream_label=_scenario_repair_stream_label(execution_state),
                ),
                prompt=prompt,
                # Модель вправе редактировать tests/**; pytest в repair-hop не запускается.
                tools=_SCENARIO_TEST_REPAIR_TOOLS,
                agent_key="scenario_test_repair",
                resume_agent_id=None,
                payload_model=ScenarioTestRepairAnswer,
                format_instructions=schema_format_reminder(ScenarioTestRepairAnswer),
                max_validation_attempts=self.validation_max_attempts,
            )
            # Explicit Pydantic validation EVEN if an adapter claims it parsed JSON.
            # No dict/agent narrative is accepted as a typed repair result.
            if not isinstance(outcome.parsed, BaseModel):
                raise ValueError("REPAIR_OUTPUT_MISSING: Pydantic parsed model absent")
            answer = ScenarioTestRepairAnswer.model_validate(
                outcome.parsed.model_dump(mode="json"),
            )
        except Exception as exc:
            # Postflight still runs after any ordinary SDK/validation failure.
            sdk_error = f"REPAIR_EXECUTION_UNPROVEN: {type(exc).__name__}: {exc}"

        # Inspect the workspace after *any* SDK return/error. This detects
        # unauthorized remaining writes, but is not OS-level write isolation.
        try:
            after = workspace_snapshot(repo)
            changed = changed_paths(before, after)
        except (OSError, ValueError, RepairSafetyError) as exc:
            return _block_patch(
                f"REPAIR_POSTFLIGHT_UNAVAILABLE: {type(exc).__name__}: {exc}",
                ids, execution_id=execution_id,
            )
        outside = disallowed_paths(changed)
        if outside:
            return _block_patch(
                f"REPAIR_SCOPE_VIOLATION: unexpected edits {outside}",
                ids, execution_id=execution_id, changed=changed,
            )
        if sdk_error or answer is None:
            return _block_patch(
                sdk_error or "REPAIR_OUTPUT_MISSING",
                ids, execution_id=execution_id, changed=changed,
            )
        if answer.status == "CANDIDATE_PENDING_VERIFY":
            for scenario_id in answer.addressed_gap_ids:
                if ensure_workflow_deferred_for_scenario(repo, change_id, scenario_id):
                    from agents.common.scenario_files import scenario_test_relpath

                    rel = scenario_test_relpath(change_id, scenario_id)
                    if rel not in changed:
                        changed.append(rel)
        error = validate_candidate(
            answer, ids, actual_changed=changed, repo=repo, change_id=change_id,
        )
        if error:
            return _block_patch(error, ids, execution_id=execution_id, changed=changed)

        merged_changed = list(dict.fromkeys(changed))
        patch: dict[str, Any] = {
            "scenario_test_repair_status": answer.status,
            "scenario_test_repair_answer": answer.model_dump(mode="json"),
            "scenario_test_repair_execution_id": execution_id,
            "scenario_test_repair_changed_paths": merged_changed,
            "scenario_test_repair_independent_verified": False,
            "scenario_test_repair_owner_attention": (
                answer.recovery_contract if answer.status != "CANDIDATE_PENDING_VERIFY" else ""
            ),
        }
        if matrix_rebind:
            patch["scenario_test_repair_matrix_rebind"] = matrix_rebind
            matrix_rel = matrix_rebind.get("matrix_path")
            if isinstance(matrix_rel, str) and matrix_rel not in merged_changed:
                patch["scenario_test_repair_changed_paths"] = [*merged_changed, matrix_rel]
        if outcome is not None:
            patch.update(agent_session_patch(
                execution_state,
                agent_id=outcome.agent_id,
                agent_id_key="scenario_test_repair_agent_id",
            ))
            patch["scenario_test_repair_sdk_run_id"] = outcome.run_id
        return patch
