"""Hermetic E1–E5 + slice acceptance for PBH review→repair reliability."""

from __future__ import annotations

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_commit_materialize,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    reset_worker_control_plane_identity_for_tests,
    verify_control_plane_worker_identity,
)
from config import TEMPORAL_TASK_QUEUE
from control_plane_code_identity import compute_control_plane_code_identity
from cursor_adapter import (
    MockCursorAdapter,
    MockScenario,
    MockScenarioStep,
    build_hunt_clean_output,
    build_hunt_contract_invalid_output,
    build_hunt_orch_clear_output,
)
from lanes import make_project_bug_hunt_lane
from loop_engine import ReviewRepairLoop
from models import REVIEW_CONTRACT_INVALID
from parsers import parse_project_bug_hunt_result
from tempo_operator import _read_worker_state, _write_worker_state, ensure_worker
from review_repair_terminal_policy import (
    TEMPORAL_MISSION_COMPLETE,
    TEMPORAL_MISSION_FAILED,
    map_project_bug_hunt_terminal,
    terminal_payload_from_application_error,
)
from workflow import ProjectBugHuntWorkflow
from pbh_workflow_test_helpers import execute_pbh_with_admission

_FIXTURE = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "yalta_max_funnel_pbh_006_007_dual_typed_fields_hunt_complete.txt"
)

_HUNT_ACTIVITIES = [
    verify_control_plane_worker_identity,
    ensure_cursor_session,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    execute_commit_materialize,
]


def _workflow_input(**overrides):
    base = {
        "mode": "mock",
        "repo_path": ".",
        "mock_scenario": "project-bug-hunt-scenario1",
        "lane": "project-bug-hunt",
        "base": "main",
        "head": "HEAD",
        "change": "UNRESOLVED_CHANGE",
        "control_plane_code_identity": compute_control_plane_code_identity(),
        "expected_candidate_sha": "HEAD",
    }
    base.update(overrides)
    return base


def test_b1_ensure_worker_missing_identity_attestation_pauses(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    _write_worker_state(repo, review="high", repair="high", pid=999999, overlay=overlay)
    import tempo_operator as op

    original_list = op.list_worker_pids
    original_listen = op._worker_listening
    op.list_worker_pids = lambda **_: [(999999, "worker.py")]
    op._worker_listening = lambda _pid, **_: True
    try:
        outcome = ensure_worker(repo, overlay, review="high", repair="high")
    finally:
        op.list_worker_pids = original_list
        op._worker_listening = original_listen
    assert outcome.get("action") == "pause"
    assert outcome.get("status") == "WORKER_CODE_IDENTITY_UNVERIFIED"
    assert "control_plane_code_identity" not in outcome or not outcome.get(
        "control_plane_code_identity"
    )


def test_b3_ensure_worker_non_listening_process_blocks_replacement(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    _write_worker_state(
        repo,
        review="high",
        repair="high",
        pid=424242,
        overlay=overlay,
        control_plane_code_identity=compute_control_plane_code_identity(),
    )
    import tempo_operator as op

    original_list = op.list_worker_pids
    original_listen = op._worker_listening
    original_popen = op.subprocess.Popen

    op.list_worker_pids = lambda **_: [(424242, "worker.py")]
    op._worker_listening = lambda _pid, **_: False
    def _forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("must not spawn replacement worker")

    op.subprocess.Popen = _forbidden_spawn
    try:
        outcome = ensure_worker(repo, overlay, review="high", repair="high")
    finally:
        op.list_worker_pids = original_list
        op._worker_listening = original_listen
        op.subprocess.Popen = original_popen
    assert outcome.get("action") == "pause"
    assert outcome.get("status") == "WORKER_NOT_LISTENING"
    assert outcome.get("pid") == 424242


def test_b2_ensure_worker_stale_identity_blocks_admission(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    _write_worker_state(
        repo,
        review="high",
        repair="high",
        pid=999999,
        overlay=overlay,
        control_plane_code_identity="0" * 64,
    )
    # No live worker process — force reuse path via monkeypatched listeners.
    import tempo_operator as op

    original_list = op.list_worker_pids
    original_listen = op._worker_listening

    op.list_worker_pids = lambda **_: [(999999, "worker.py")]
    op._worker_listening = lambda _pid, **_: True
    try:
        outcome = ensure_worker(repo, overlay, review="high", repair="high")
    finally:
        op.list_worker_pids = original_list
        op._worker_listening = original_listen
    assert outcome.get("status") == "STALE_WORKER"
    state = _read_worker_state(repo)
    assert state is not None
    assert state.get("control_plane_code_identity") == "0" * 64


def test_a1b_incident_semantics_classification_and_severity_preserved():
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures"
        / "project_bug_hunt"
        / "yalta_pbh_006_classification_severity_dual_repr.txt"
    )
    parsed = parse_project_bug_hunt_result(path.read_text(encoding="utf-8"))
    assert parsed.completed is True
    assert len(parsed.findings) == 1
    row = parsed.findings[0]
    assert row["finding_id"] == "PBH-006"
    assert row.get("production_classification") == "confirmed production bug"
    assert row.get("severity_raw") == "medium"
    assert row["severity"] == "Medium"


def test_a1_pbh_006_007_dual_typed_canonical_tail_resolves_two_findings():
    raw = _FIXTURE.read_text(encoding="utf-8")
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.parse_error_code is None
    assert len(parsed.findings) == 2
    assert [row["finding_id"] for row in parsed.findings] == ["PBH-006", "PBH-007"]
    assert parsed.normalization_decisions


def test_a3_conflicting_severity_on_same_id_fails_closed():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 1

### FINDING_ID: PBH-006
- SEVERITY: High
- TITLE: A
- LOC_LINK: tests/a.py:1

### FINDING_ID: PBH-006
- SEVERITY: Low
- TITLE: A
- LOC_LINK: tests/a.py:1
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert any("conflicting SEVERITY" in err for err in parsed.parse_errors)


def test_a4_conflicting_title_fails_closed():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 1

### FINDING_ID: PBH-006
- SEVERITY: High
- TITLE: First title
- LOC_LINK: tests/a.py:1

### FINDING_ID: PBH-006
- SEVERITY: High
- TITLE: Other title
- LOC_LINK: tests/a.py:1
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID


def test_a5_three_findings_with_total_two_invalid():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 2

### FINDING_ID: PBH-001
- LOC_LINK: tests/a.py:1
### FINDING_ID: PBH-002
- LOC_LINK: tests/b.py:2
### FINDING_ID: PBH-003
- LOC_LINK: tests/c.py:3
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID


def test_a6_valid_two_findings_single_repair_batch():
    raw = _FIXTURE.read_text(encoding="utf-8")
    hunt = make_project_bug_hunt_lane(change="invest-yalta-max-funnel")
    adapter = MockCursorAdapter(
        MockScenario(
            name="a6-repair-dispatch",
            steps=[
                MockScenarioStep(hunt.review_prompt, raw),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
            ],
        )
    )
    outcome = ReviewRepairLoop(adapter, lane=hunt, mock_mode=True).run()
    assert outcome.repair_rounds == 1
    assert len(adapter.history) == 2


def test_a7_invalid_review_zero_repair_batches():
    hunt = make_project_bug_hunt_lane(change="invest-yalta-max-funnel")
    adapter = MockCursorAdapter(
        MockScenario(
            name="a7-invalid",
            steps=[MockScenarioStep(hunt.review_prompt, build_hunt_contract_invalid_output())],
        )
    )
    outcome = ReviewRepairLoop(adapter, lane=hunt, mock_mode=True).run()
    assert outcome.status == REVIEW_CONTRACT_INVALID
    assert outcome.repair_rounds == 0
    assert len(adapter.history) == 1


def test_c2_review_contract_invalid_maps_to_temporal_failure():
    mapping = map_project_bug_hunt_terminal(REVIEW_CONTRACT_INVALID)
    assert mapping.temporal_execution_failed is True
    assert mapping.temporal_status == TEMPORAL_MISSION_FAILED


def test_c1_hunt_clean_maps_to_temporal_success():
    mapping = map_project_bug_hunt_terminal("HUNT_CLEAN")
    assert mapping.temporal_execution_failed is False
    assert mapping.temporal_status == TEMPORAL_MISSION_COMPLETE


@pytest.mark.asyncio
async def test_e1_exact_one_repair_cycle_hunt_clean():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-one-repair-cycle",
            lane="project-bug-hunt",
            base="main",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            result = await execute_pbh_with_admission(
                env.client,
                _workflow_input(mock_scenario="project-bug-hunt-one-repair-cycle"),
                workflow_id="pbh-e1-exact",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] == "HUNT_CLEAN"
    assert result["temporal_status"] == TEMPORAL_MISSION_COMPLETE
    assert result["review_runs"] == 2
    assert result["repair_rounds"] == 1
    assert result.get("last_materialize", {}).get("ok") is True


@pytest.mark.asyncio
async def test_e1_multi_round_convergence_still_supported():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-scenario1",
            lane="project-bug-hunt",
            base="main",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            result = await execute_pbh_with_admission(
                env.client,
                _workflow_input(),
                workflow_id="pbh-e1-multi",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] == "HUNT_CLEAN"
    assert result["review_runs"] == 3
    assert result["repair_rounds"] == 2


@pytest.mark.asyncio
async def test_e2_invalid_hunt_terminal_contract_failure_visible():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-contract-invalid",
            lane="project-bug-hunt",
            base="stage",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            with pytest.raises(WorkflowFailureError) as excinfo:
                await execute_pbh_with_admission(
                    env.client,
                    _workflow_input(
                        mock_scenario="project-bug-hunt-contract-invalid",
                        head="HEAD",
                        change="UNRESOLVED_CHANGE",
                        expected_candidate_sha="HEAD",
                    ),
                    workflow_id="pbh-e2-invalid",
                    task_queue=TEMPORAL_TASK_QUEUE,
                )
    payload = terminal_payload_from_application_error(excinfo.value.cause)
    assert payload is not None
    assert payload["status"] == REVIEW_CONTRACT_INVALID
    assert payload["repair_rounds"] == 0
    assert payload["review_runs"] == 1
    assert payload["temporal_status"] == TEMPORAL_MISSION_FAILED
    assert payload.get("last_materialize") is None


@pytest.mark.asyncio
async def test_e4_stale_worker_identity_blocks_before_first_hunt():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-scenario1",
            lane="project-bug-hunt",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            with pytest.raises(WorkflowFailureError) as excinfo:
                await execute_pbh_with_admission(
                    env.client,
                    _workflow_input(control_plane_code_identity="deadbeef" * 8),
                    workflow_id="pbh-e4-stale",
                    task_queue=TEMPORAL_TASK_QUEUE,
                )
    payload = terminal_payload_from_application_error(excinfo.value.cause)
    assert payload is not None
    assert payload["status"] == "STALE_WORKER"
    assert payload["review_runs"] == 0
    assert payload["repair_rounds"] == 0
    assert payload["temporal_status"] == "REVIEW_REPAIR_MISSION_PAUSED"


def test_e5_admission_manifest_rejects_independent_candidate_mismatch(tmp_path):
    from pbh_admission_binding import (
        publish_admission_manifest_atomic,
        validate_admission_binding,
    )

    repo = tmp_path
    publish_admission_manifest_atomic(
        repo,
        workflow_id="pbh-e5-binding",
        workflow_run_id="run-independent-001",
        expected_candidate_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        control_plane_code_identity=compute_control_plane_code_identity(),
    )
    manifest = __import__("pbh_admission_binding").load_admission_manifest(
        repo, "pbh-e5-binding", "run-independent-001"
    )
    err, message = validate_admission_binding(
        manifest,
        workflow_id="pbh-e5-binding",
        workflow_run_id="run-independent-001",
        observed_candidate_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        expected_control_plane_code_identity=compute_control_plane_code_identity(),
    )
    assert err == "WORKER_CODE_IDENTITY_UNVERIFIED"
    assert "candidate_sha" in message


@pytest.mark.asyncio
async def test_e5_workflow_rejects_missing_admission_manifest():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-one-repair-cycle",
            lane="project-bug-hunt",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            with pytest.raises(WorkflowFailureError) as excinfo:
                await env.client.execute_workflow(
                    ProjectBugHuntWorkflow.run,
                    _workflow_input(mock_scenario="project-bug-hunt-one-repair-cycle"),
                    id="pbh-e5-no-manifest",
                    task_queue=TEMPORAL_TASK_QUEUE,
                )
    payload = terminal_payload_from_application_error(excinfo.value.cause)
    assert payload is not None
    assert payload["status"] == "WORKER_CODE_IDENTITY_UNVERIFIED"


def test_c3_pause_status_is_temporal_failure():
    mapping = map_project_bug_hunt_terminal("STALE_WORKER")
    assert mapping.temporal_execution_failed is True
    assert mapping.bucket == "PAUSE"


def test_start_exit_gate_nonzero_on_mission_pause():
    from start import temporal_cli_exit_code

    assert temporal_cli_exit_code("project-bug-hunt", {"status": "STALE_WORKER"}) == 1
    assert temporal_cli_exit_code("project-bug-hunt", {"status": "HUNT_CLEAN"}) == 0


def test_e3_stalled_fingerprint_stops_without_false_pass():
    raw = _FIXTURE.read_text(encoding="utf-8")
    hunt = make_project_bug_hunt_lane(change="invest-yalta-max-funnel")
    adapter = MockCursorAdapter(
        MockScenario(
            name="e3-stall",
            steps=[
                MockScenarioStep(hunt.review_prompt, raw),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
                MockScenarioStep(hunt.review_prompt, raw),
            ],
        )
    )
    outcome = ReviewRepairLoop(adapter, lane=hunt, mock_mode=True).run()
    assert outcome.status in {
        "STALLED",
        "NO_REPAIR_PROGRESS",
        "MAX_ROUNDS_REACHED",
        "PAUSE_RECOVERY_UNRECONCILED",
    }
    assert outcome.status != "HUNT_CLEAN"
