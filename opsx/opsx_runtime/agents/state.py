"""Shared LangGraph state: Bugbot review и PHASE_IMPL verify outcomes."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BugBotFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "high", "medium", "low"]
    location: str = Field(min_length=1, description="file:line of the defect")
    finding: str = Field(min_length=1, description="Trigger -> cause -> observable consequence")


class BugBotAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["CLEAN", "FINDINGS", "BLOCKED", "NO_DIFF"]
    findings: list[BugBotFinding] = Field(default_factory=list)
    block_reason: str | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> "BugBotAnswer":
        if self.status == "FINDINGS" and not self.findings:
            raise ValueError("FINDINGS requires at least one finding")
        if self.status != "FINDINGS" and self.findings:
            raise ValueError("findings allowed only with FINDINGS status")
        if self.status == "BLOCKED" and not self.block_reason:
            raise ValueError("BLOCKED requires block_reason")
        return self


class GraphState(TypedDict, total=False):
    planning_readiness_status: str
    planning_readiness_result: dict[str, Any]
    scenario_authoring_status: str
    scenario_authoring_result: dict[str, Any]
    verify_recovery_contract: str
    plan_artifact_status: str
    plan_artifact_result: dict[str, Any]
    plan_artifact_source_digest: str | None
    plan_artifact_materialization_result: dict[str, Any]
    repository_path: str
    workspace: str
    model: dict[str, Any]
    run_uuid: str | None
    # Cross-execution, content-bound check receipt provenance (not a graph cache).
    check_receipt: dict[str, Any]
    coverage_activation_receipt: dict[str, Any]
    quality_gate_receipt: dict[str, Any]
    # Required for safe quality-gate reuse: caller attests external/DB fixture
    # identity. If missing, real pytest/pyright/mypy always execute.
    quality_gate_environment_id: str | None
    workflow_execution_id: str | None
    agent_id: str | None
    session_run_uuid: str | None
    isolated_workspace: bool
    review: dict[str, Any]
    run_id: str | None
    execution_status: str
    usage: dict[str, int | None] | None
    cost: dict[str, float] | None
    cost_status: str
    billing_error: str | None
    usage_source: str
    error: str | None
    stream_log: str
    stream_label: str
    change_id: str
    bug_ids: dict[str, str]
    mode: str
    verdict: str
    marker: str
    report_text: str
    answer: dict[str, Any]
    deterministic_preflight: dict[str, Any]
    quality_gate_raw: dict[str, Any]
    last_pytest_execute_by_scope: dict[str, Any]
    pytest_execute_finished_scopes: list[str]
    quality_gate_diagnostics: dict[str, Any]
    quality_gate_verdict: str
    quality_gate_marker: str
    quality_gate_report_text: str
    quality_gates_stream_log: str
    quality_gates_stream_label: str
    quality_gates_agent_id: str | None
    quality_gates_run_id: str | None
    quality_gates_execution_status: str
    quality_gates_usage: dict[str, int | None] | None
    quality_gates_cost: dict[str, float] | None
    quality_gates_cost_status: str
    quality_gates_billing_error: str | None
    quality_gates_usage_source: str
    static_repair_status: str
    static_repair_marker: str
    static_repair_report_text: str
    static_repair_queue: list[dict[str, str]]
    static_repair_issue_count: int
    analysis_result: dict[str, Any]
    group_error_stream_log: str
    group_error_stream_label: str
    group_error_agent_id: str | None
    group_error_session_run_uuid: str | None
    group_error_run_id: str | None
    group_error_execution_status: str
    group_error_usage: dict[str, int | None] | None
    group_error_cost: dict[str, float] | None
    group_error_cost_status: str
    group_error_billing_error: str | None
    group_error_usage_source: str
    group_error_validation_attempts: int
    repair_order_result: dict[str, Any]
    repair_order_stream_log: str
    repair_order_stream_label: str
    repair_order_agent_id: str | None
    repair_order_session_run_uuid: str | None
    repair_order_run_id: str | None
    repair_order_execution_status: str
    repair_order_usage: dict[str, int | None] | None
    repair_order_cost: dict[str, float] | None
    repair_order_cost_status: str
    repair_order_billing_error: str | None
    repair_order_usage_source: str
    repair_order_validation_attempts: int
    diagnostic_result: dict[str, Any]
    diagnostic_stream_log: str
    diagnostic_stream_label: str
    diagnostic_agent_id: str | None
    diagnostic_session_run_uuid: str | None
    diagnostic_run_id: str | None
    diagnostic_execution_status: str
    diagnostic_usage: dict[str, int | None] | None
    diagnostic_cost: dict[str, float] | None
    diagnostic_cost_status: str
    diagnostic_billing_error: str | None
    diagnostic_usage_source: str
    diagnostic_validation_attempts: int
    dispatcher_pass_initialized: bool
    dispatcher_pass_queue: list[dict[str, Any]]
    dispatcher_pass_index: int
    dispatcher_pass_complete: bool
    dispatcher_cycle_status: str
    dispatcher_current_item: dict[str, Any] | None
    dispatcher_route: str | None
    dispatcher_outcomes: list[dict[str, Any]]
    dispatcher_resolved_finding_ids: list[str]
    dispatcher_unit_triage_complete: bool
    verify_dispatch_mode: str
    dispatch_causal_groups: list[dict[str, Any]]
    dispatcher_group_index: int
    dispatcher_group_phase: str
    dispatch_unit_triage_result: dict[str, Any] | None
    dispatch_group_verify_result: dict[str, Any] | None
    dispatcher_current_group: dict[str, Any] | None
    static_triage_result: dict[str, Any]
    static_triage_stream_log: str
    static_triage_stream_label: str
    static_triage_agent_id: str | None
    static_triage_session_run_uuid: str | None
    static_triage_run_id: str | None
    static_triage_execution_status: str
    static_triage_usage: dict[str, int | None] | None
    static_triage_cost: dict[str, float] | None
    static_triage_cost_status: str
    static_triage_billing_error: str | None
    static_triage_usage_source: str
    static_triage_validation_attempts: int
    verify_dispatch_path: str
    verify_finding_fix: dict[str, Any]
    verify_finding_repair_outcome: dict[str, Any]
    verify_finding_commit_outcome: dict[str, Any]
    verify_finding_finalize_outcome: dict[str, Any]
    typing_repair_result: dict[str, Any]
    typing_repair_agent_id: str | None
    typing_repair_session_run_uuid: str | None
    typing_repair_run_id: str | None
    typing_repair_execution_status: str
    typing_repair_usage: dict[str, int | None] | None
    typing_repair_cost: dict[str, float] | None
    typing_repair_cost_status: str
    typing_repair_billing_error: str | None
    typing_repair_usage_source: str
    typing_repair_validation_attempts: int
    test_triage_result: dict[str, Any]
    test_triage_stream_log: str
    test_triage_stream_label: str
    test_triage_agent_id: str | None
    test_triage_session_run_uuid: str | None
    test_triage_run_id: str | None
    test_triage_execution_status: str
    test_triage_usage: dict[str, int | None] | None
    test_triage_cost: dict[str, float] | None
    test_triage_cost_status: str
    test_triage_billing_error: str | None
    test_triage_usage_source: str
    test_triage_validation_attempts: int
    test_duration_result: dict[str, Any]
    test_duration_stream_log: str
    test_duration_agent_id: str | None
    test_duration_session_run_uuid: str | None
    duration_probe_exhausted: bool
    duration_probe_keys: list[str]
    single_repro_probe_keys: list[str]
    pytest_single_repro_result: dict[str, Any]
    pytest_single_repro_skipped: bool
    test_interaction_result: dict[str, Any]
    test_interaction_stream_log: str
    test_interaction_agent_id: str | None
    test_interaction_session_run_uuid: str | None
    test_interaction_run_id: str | None
    test_interaction_execution_status: str
    test_interaction_usage: dict[str, int | None] | None
    test_interaction_cost: dict[str, float] | None
    test_interaction_cost_status: str
    test_interaction_billing_error: str | None
    test_interaction_usage_source: str
    test_interaction_validation_attempts: int
    test_interaction_sequence_evidence: dict[str, Any]
    batch_recheck_count: int
    verify_batch_recheck_required: bool
    verify_force_fresh_quality_gates: bool
    finding_commit_revision_by_finding: dict[str, str]
    verify_finding_commit_revision_key: str | None
    personal_timeout_marker_seconds: int | None
    personal_timeout_probe: dict[str, Any]
    test_repair_result: dict[str, Any]
    test_repair_agent_id: str | None
    test_repair_session_run_uuid: str | None
    test_repair_run_id: str | None
    test_repair_execution_status: str
    test_repair_usage: dict[str, int | None] | None
    test_repair_cost: dict[str, float] | None
    test_repair_cost_status: str
    test_repair_billing_error: str | None
    test_repair_usage_source: str
    test_repair_validation_attempts: int
    verify_outer_iteration: int
    verify_outer_max_iterations: int
    verify_outer_batch_active: bool
    verify_outer_batch_final_pending: bool
    verify_outer_batch_start_failures: list[str]
    verify_last_invalid_fingerprints: list[str]
    verify_last_invalid_reason: str
    verify_outer_route_decision: str | None
    verify_harness_recovery_attempts: int
    pytest_error_classifier_result: dict[str, Any]
    test_harness_repair_result: dict[str, Any]
    test_harness_repair_agent_id: str | None
    environment_prep_result: dict[str, Any]
    environment_prep_attempts: int
    environment_prep_execution_status: str
    environment_prep_agent_id: str | None
    environment_prep_run_id: str | None
    environment_prep_resolved_finding_ids: list[str]
    verify_outer_batch_stall_key: str | None
    verify_outer_batch_stall_count: int
    verify_loop_status: str
    verify_loop_terminal: bool
    verify_workflow_code_changed: bool
    verify_continuation: dict[str, Any]
    verify_green_restart_count: int
    verify_green_max_restarts: int
    verify_cohort_restart_count: int
    verify_cohort_max_restarts: int
    verify_post_reset_route: str | None
    verify_stop_reason: str
    owner_attention: str
    project_bug_hunt_status: str
    """Счётчик repair-циклов subgraph project_bug (диагностика)."""
    project_bug_check_loop_pass: int
    bug_check_repair_status: str
    bug_check_repair_mission_id: str
    bug_check_repair_error: str | None
    bug_check_repair_finished_count: int
    """Исход nested bug-team repair hop (LangGraph требует явных ключей в схеме)."""
    bug_check_repair_workflow_outcome: str
    bug_check_repair_next_allowed: str
    bug_check_repair_stop_kind: str
    """VERIFY_PAUSED на check-repair: pause до bugbot/security."""
    verify_workflow_paused: bool
    verify_pause_reason: str | None
    infra_tests_status: str
    infra_tests_result: dict[str, Any]
    security_review_status: str
    security_check_loop_pass: int
    security_check_repair_status: str
    security_check_repair_mission_id: str
    security_check_repair_error: str | None
    security_check_repair_finished_count: int
    security_check_repair_workflow_outcome: str
    security_check_repair_next_allowed: str
    security_check_repair_stop_kind: str
    test_workflow_placeholder_result: dict[str, Any]
    apply_status: str
    apply_marker: str
    apply_report_text: str
    apply_answer: dict[str, Any]
    apply_stream_log: str
    apply_stream_label: str
    apply_validation_attempts: int
    verify_apply_restart_target: str
    verify_apply_gap_source: str
    verify_apply_block_reason: str
    verify_check_circle_code_changed: bool
    bugbot_check_loop_pass: int
    bugbot_level: int
    security_level: int
    verify_scenario_episode_used_apply: bool
    verify_scenario_apply_run_uuid: str
    verify_scenario_episode_used_repair: bool
    verify_scenario_repair_run_uuid: str
    verify_scenario_repair_audit_run_uuid: str
    scenario_test_repair_graph_hop: bool
    commit_status: str
    commit_sha: str | None
    commit_files: list[str]
    cycle_checks: dict[str, Any]
    verify_cycle_skipped: bool
    scenario_coverage_verdict: str
    scenario_coverage_marker: str
    scenario_coverage_report_text: str
    scenario_coverage_answer: dict[str, Any]
    scenario_coverage_agent_id: str | None
    scenario_coverage_run_id: str | None
    scenario_coverage_execution_status: str
    scenario_coverage_usage: dict[str, int | None] | None
    scenario_coverage_cost: dict[str, float] | None
    scenario_coverage_cost_status: str
    scenario_coverage_billing_error: str | None
    scenario_coverage_usage_source: str
    scenario_coverage_validation_attempts: int
    scenario_coverage_source_snapshot: dict[str, Any]
    scenario_coverage_convergence: dict[str, Any]
    scenario_coverage_execution_id: str
    scenario_coverage_receipt: dict[str, Any]
    # Observable OPSX row reuse/dispatch decisions are part of durable
    # checkpoint state; stderr progress is only operational telemetry.
    scenario_coverage_selection: list[dict[str, Any]]
    scenario_coverage_model_dispatches: int
    scenario_coverage_sdk_calls: int
    # Scenario test-only repair hop (verify graph + optional live/pinned callers).
    scenario_test_repair_authorized: bool
    scenario_test_repair_status: str
    scenario_test_repair_answer: dict[str, Any]
    scenario_test_repair_owner_attention: str
    scenario_test_repair_execution_id: str
    scenario_test_repair_independent_verified: bool
    scenario_test_repair_changed_paths: list[str]
    scenario_test_repair_agent_id: str | None
    scenario_test_repair_session_run_uuid: str
    scenario_test_repair_sdk_run_id: str
    coverage_activation_verdict: str
    coverage_activation_marker: str
    coverage_activation_report_text: str
    coverage_activation_answer: dict[str, Any]
    coverage_activation_inventory: dict[str, Any]
    coverage_activation_metrics: dict[str, Any]
    coverage_activation_checks: dict[str, Any]
    coverage_activation_fingerprint: str
    coverage_activation_apply_code_changed: bool
    coverage_activation_complete: bool
    coverage_activation_accumulated_touched: list[str]
    coverage_activation_skipped_resume: bool
    coverage_activation_agent_id: str | None
    coverage_activation_run_id: str | None
    coverage_activation_execution_status: str
    coverage_activation_stream_log: str
    coverage_activation_stream_label: str
    implementation_stage: str | None
    implementation_slice_id: str | None
    """Упорядоченные slice-объекты после узла ``implementation_slice_list``."""
    implementation_slices: list[dict[str, Any]]
    """Индекс текущего слайса в ``implementation_slices``."""
    implementation_slice_index: int
    """Сколько раз зелёный вернул задачу в design (макс. 1)."""
    implementation_design_redo_count: int
    """Сколько раз design lane уже поставила APPLY_DESIGN (макс. 3, затем слайсы)."""
    implementation_design_attempt: int
    """SHA-256 канона при последнем fail submit (CAS для счётчика петли)."""
    implementation_design_canon_sha256: str | None
    """Подряд идентичные fail submit на том же каноне (``DESIGN_INCOMPLETE`` и др.)."""
    implementation_design_submit_fail_streak: int
    """Код последнего fail submit на текущем каноне."""
    implementation_design_submit_fail_code: str | None
    implementation_status: str
    implementation_recovery_code: str | None
    implementation_plan_reject_code: str | None
    implementation_unchanged_dispatch_count: int
    """После hop stagnation: route узел переиспользует ``implementation_plan`` без повторного subprocess."""
    implementation_plan_reuse_after_hop: bool
    """True: plan обновил state, SDK hop в этом заходе не запускался (для conditional edge)."""
    implementation_plan_applied_without_hop: bool
    """Recovery contract из plan PAUSED (``RECOVERY_PAUSED`` и др.)."""
    implementation_recovery_contract: dict[str, Any] | None
    """Результат ``validate_red_operative`` для текущего slice (очищается после PASS / смены slice)."""
    apply_red_operative_validation: dict[str, Any] | None
    """Machine gate errors (RED implement validators) for the current stage/slice hop."""
    implementation_machine_gate_errors: list[dict[str, Any]] | None
    """One corrective SDK pass after machine validator FAIL in the same stage."""
    implementation_hop_machine_repair_count: int
    """Сколько раз RED materialize отказал на текущем слайсе.

    Поле обязано быть в ``GraphState``: LangGraph отбрасывает ключи вне схемы,
    и ``route_after_slice_stage`` тогда не видит первый повтор (``retry_red``).
    ``1`` — один повтор RED; ``>1`` — PAUSE.
    """
    implementation_red_oracle_retry_count: int
    implementation_primitive_command: str | None
    implementation_plan: dict[str, Any]
    implementation_plan_override: dict[str, Any]
    implementation_last_hop: str | None
    implementation_dry_run: bool
    implementation_lane_complete: bool
    """Последний hop прошёл receipt gate без SDK (``SKIP``) или после SDK (``SDK``)."""
    implementation_receipt_gate: str | None
    """Готовый JSON ``SliceReceiptValidationAnswer`` для текущего hop (модель не пересчитывает)."""
    implementation_slice_receipt_validation: dict[str, Any] | None
    """Корневая среда: не обнулять operative plan при bootstrap implementation lane."""
    implementation_receipt_skip: bool
    """Design subgraph завершён и отдал forward plan основному apply loop."""
    apply_design_lane_complete: bool
    """Code lane просит заново выполнить design; slice стартует после нового design."""
    implementation_force_design_hop: bool
    implementation_arbiter_pending: bool
    implementation_arbiter_request: dict[str, Any] | None
    implementation_arbiter_calls: int
    implementation_arbiter_directive: str | None
    implementation_arbiter_target_stage: str | None
    implementation_arbiter_answer: dict[str, Any] | None
    implementation_arbiter_directed_design_hop: bool
    implementation_arbiter_agent_id: str | None
    implementation_arbiter_session_run_uuid: str
    live_execution_authorized: bool
    skip_implementation: bool
    artifact_bundle: dict[str, Any]
