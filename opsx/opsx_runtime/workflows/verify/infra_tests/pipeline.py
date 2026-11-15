"""Детерминированный прогон infra_tests: выбор, среда, результат, teardown.

Продуктовые тесты не редактируются. Node id приходят в state. Пустой выбор — PASS_NO_SCOPE
без docker. При выбранных nodes и недоступной среде — UNAVAILABLE и raise. Lease закрывается и при ошибке.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.state import GraphState
from infra.engine import InfraEnvSnapshot, apply_decision, release_lease
from workflows.stop_errors import InfraTestsStageError
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.verify.infra_tests.agents import InfraEnvDoctor, InfraTestTriage
from workflows.verify.infra_tests.infra_tests_receipt import InfraTestsDurablePass
from workflows.verify.infra_tests.selection import select_infra_nodes

PASS_STATUSES = frozenset({"PASS", "PASS_NO_SCOPE"})


def _snapshot_from_state(state: GraphState) -> InfraEnvSnapshot:
    raw = state.get("infra_env_snapshot")
    if isinstance(raw, InfraEnvSnapshot):
        return raw
    if isinstance(raw, dict):
        return InfraEnvSnapshot(
            profile_name=str(raw.get("profile_name") or "unspecified"),
            docker_ok=bool(raw.get("docker_ok")),
            compose_v2=bool(raw.get("compose_v2")),
            port_open=bool(raw.get("port_open")),
            db_comment=raw.get("db_comment") if isinstance(raw.get("db_comment"), str) else None,
            expected_comment=str(raw.get("expected_comment") or "stroy-disposable-test-db"),
            alembic_at_head=raw.get("alembic_at_head") if isinstance(raw.get("alembic_at_head"), bool) else None,
        )
    return InfraEnvSnapshot(
        profile_name="unspecified",
        docker_ok=False,
        compose_v2=False,
        port_open=False,
        db_comment=None,
        expected_comment="stroy-disposable-test-db",
    )


def _tuple(state: GraphState, key: str) -> tuple[str, ...]:
    raw = state.get(key)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


async def infra_tests_node(state: GraphState) -> dict[str, Any]:
    """Узел verify между bugbot и security."""
    return await run_infra_tests(state)


async def run_infra_tests(state: GraphState) -> dict[str, Any]:
    """Поднять или усыновить среду и вернуть статус. FAIL и BLOCKED_ENV — raise.

    Teardown lease выполняется до исключения, чтобы сироты не оставались без записи.
    """
    selection = select_infra_nodes(
        trace_nodes=_tuple(state, "infra_trace_nodes"),
        slice_nodes=_tuple(state, "infra_slice_nodes"),
        import_closure_nodes=_tuple(state, "infra_import_nodes"),
        all_infra_nodes=_tuple(state, "infra_all_nodes"),
        full_triggers_hit=bool(state.get("infra_full_triggers_hit")),
        force_full=bool(state.get("infra_force_full")),
        disabled_nodes=_tuple(state, "infra_disabled_nodes"),
    )
    repo_root = resolve_opsx_repository_root(
        Path(str(state.get("repository_path") or state.get("workspace") or ".")),
    )
    change_id = str(state.get("change_id") or "unknown")
    node_key = tuple(selection.node_ids)
    if selection.status == "PASS_NO_SCOPE":
        cached = InfraTestsDurablePass.read_cached(
            state,
            root=repo_root,
            change_id=change_id,
            node_ids=node_key,
            pass_status="PASS_NO_SCOPE",
        )
        if cached is not None:
            return cached
        patch = {
            "infra_tests_status": "PASS_NO_SCOPE",
            "infra_tests_result": {
                "selection": list(selection.node_ids),
                "reasons": list(selection.reasons),
                "decision": "SKIP_NO_SCOPE",
                "not_run": [],
            },
        }
        InfraTestsDurablePass.save_pass(
            state,
            root=repo_root,
            change_id=change_id,
            node_ids=node_key,
            patch=patch,
            pass_status="PASS_NO_SCOPE",
        )
        return patch
    snapshot = _snapshot_from_state(state)
    lease = apply_decision(snapshot, run_id=str(state.get("run_uuid") or "infra"))
    doctor = InfraEnvDoctor()
    triage = InfraTestTriage()
    try:
        if lease.decision in {"UNAVAILABLE", "START_ALT_PORT"}:
            _ = doctor
            raise InfraTestsStageError(
                f"infra environment {lease.decision}",
                code="BLOCKED_ENV",
                evidence="; ".join(lease.notes),
                next_action="start disposable postgres on a free port or free 55432 without killing a foreign database",
            )
        failure_owner = str(state.get("infra_failure_owner") or "")
        if failure_owner == "UNKNOWN":
            _ = triage.run({"node_ids": list(selection.node_ids)})
        if failure_owner and failure_owner != "PASS":
            raise InfraTestsStageError(
                f"infra tests failed owner={failure_owner}",
                code="INFRA_TESTS_FAIL",
                evidence=str(list(selection.node_ids)),
                next_action="repair the owner class and rerun the failed nodes",
            )
        cached_pass = InfraTestsDurablePass.read_cached(
            state,
            root=repo_root,
            change_id=change_id,
            node_ids=node_key,
            pass_status="PASS",
        )
        if cached_pass is not None:
            return cached_pass
        patch = {
            "infra_tests_status": "PASS",
            "infra_tests_result": {
                "selection": list(selection.node_ids),
                "reasons": list(selection.reasons),
                "decision": lease.decision,
                "llm_hops": 0,
            },
        }
        InfraTestsDurablePass.save_pass(
            state,
            root=repo_root,
            change_id=change_id,
            node_ids=node_key,
            patch=patch,
            pass_status="PASS",
        )
        return patch
    finally:
        _ = release_lease(lease)
