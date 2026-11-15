"""Валидация mapped pytest nodes для repair-hop без subprocess pytest."""
from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path
from typing import Any

from agents.repair.scenario_test_repair.live_gate import LiveGateBlocked
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    partition_workflow_pytest_nodes,
    validate_mapped_workflow_deferred_marker,
)
from agents.verify.scenario_coverage_verify.evidence_contract import trace_mappings


def mapped_test_nodes(repo: Path, change_id: str, gap_ids: list[str]) -> list[str]:
    """No invented tests: derive concrete pytest nodes from current frozen mapping."""
    matrix = trace_mappings(repo, change_id)
    result: list[str] = []
    for sid in gap_ids:
        node = matrix.get(sid)
        if not node:
            raise LiveGateBlocked(f"LIVE_TEST_MAPPING_MISSING: {sid}")
        path = node.split("::", 1)[0]
        source = (resource_path(repo, path)).resolve()
        if (
            not path.startswith("tests/")
            or not path.endswith(".py")
            or "::" not in node
            or ".." in Path(path).parts
            or not source.is_relative_to((resource_path(repo, "tests")).resolve())
            or not source.is_file()
        ):
            raise LiveGateBlocked(f"LIVE_TEST_NODE_INVALID: {node}")
        if node not in result:
            result.append(node)
    if not result:
        raise LiveGateBlocked("LIVE_TEST_NODES_EMPTY")
    return result


async def run_focused_checks(
    repo: Path, change_id: str, gap_ids: list[str], *, execution_id: str,
    db_proof_path: str | None = None,
) -> dict[str, Any]:
    """Проверить mapped nodes и маркеры; repair-hop не поднимает pytest subprocess.

    Args:
        repo: Корень репозитория с ``tests/**``.
        change_id: Идентификатор OpenSpec change.
        gap_ids: ``scenario_id`` из audit gaps (frozen trace matrix).
        execution_id: ``run_uuid`` verify-hop (для live-контрактов вне графа).
        db_proof_path: Не используется; оставлен для совместимости live acceptance.

    Returns:
        Receipt со ``status`` ``BLOCKED`` (маркер/маппинг) или ``DEFERRED``
        (pytest не запускался, ``exit_code`` всегда ``None``).
    """
    _ = db_proof_path
    _ = execution_id
    nodes = mapped_test_nodes(repo, change_id, gap_ids)
    for node in nodes:
        marker_problem = validate_mapped_workflow_deferred_marker(repo, node)
        if marker_problem:
            return {
                "status": "BLOCKED",
                "reason": marker_problem,
                "test_nodes": nodes,
                "exit_code": None,
                "db_receipt": None,
            }
    runnable, deferred = partition_workflow_pytest_nodes(repo, nodes)
    reason = (
        "WORKFLOW_SCENARIO_NOT_EXECUTABLE"
        if deferred and not runnable
        else "WORKFLOW_REPAIR_PYTEST_SKIPPED"
    )
    payload: dict[str, Any] = {
        "status": "DEFERRED",
        "reason": reason,
        "test_nodes": nodes,
        "exit_code": None,
        "db_receipt": None,
    }
    if deferred:
        payload["deferred_nodes"] = deferred
    if runnable:
        payload["skipped_runnable_nodes"] = runnable
    return payload
