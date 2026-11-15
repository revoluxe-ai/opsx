"""Scoped ``pytest_execute`` перед ``test_repair`` (per-finding dispatch)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.verify.dispatcher.items import parse_path_line
from agents.verify.dispatch_unit.repro_cohort import pytest_module, pytest_node_id
from agents.verify.dispatch_unit.repro_cohort import safe_pytest_modules
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.verify.pytest_execute import PytestExecuteRequest
from workflows.verify.pytest_execute.invoke import pytest_execute_state_patch, run_pytest_execute


def _module_paths_for_item(state: GraphState, item: dict[str, Any]) -> list[str]:
    """Относительные module paths для scoped pytest execute."""
    repo_raw = str(state.get("repository_path") or state.get("workspace") or ".")
    try:
        repo_root = resolve_opsx_repository_root(Path(repo_raw))
    except ValueError:
        return []
    obs = str(item.get("observation") or "")
    node = pytest_node_id(obs)
    rel: list[str] = []
    if node:
        mod = pytest_module(node)
        rel.append(mod)
    else:
        path, _ = parse_path_line(obs)
        if path:
            rel.append(path)
    wf = str(item.get("repair_workflow") or "")
    if not rel and (
        wf.startswith("WF_PYTEST_COLLECTION")
        or wf.startswith("WF_PYTEST_HARNESS")
        or "HermeticBoundaryError" in obs
        or obs.startswith("pytest: KeyError")
    ):
        # Collection / harness gate snapshot — нет node id; pre-repair = full collect+run.
        return []
    if not rel:
        return []
    modules = safe_pytest_modules(repo_root, rel)
    out: list[str] = []
    for m in modules:
        p = Path(m)
        try:
            out.append(str(p.relative_to(repo_root)))
        except ValueError:
            out.append(str(m))
    return out


async def run_pytest_execute_pre_repair_node(
    state: GraphState,
    *,
    harness_repair_agent: Any | None,
    repair_adapter: Any | None,
) -> dict[str, Any]:
    """Collect + harness + scoped pytest run; PASS → skip последующий test_repair.

    Returns:
        Patch с ``pytest_execute_pre_repair_result`` и ``verify_skip_test_repair`` при PASS.
    """
    item = state.get("dispatcher_current_item")
    if not isinstance(item, dict):
        raise ValueError("dispatcher_current_item required for pytest_execute_pre_repair")
    repo_raw = str(state.get("repository_path") or state.get("workspace") or ".")
    try:
        repo_root = resolve_opsx_repository_root(Path(repo_raw))
    except ValueError:
        return {
            "pytest_execute_pre_repair_result": {"status": "PAUSE", "detail": "no_repo_root"},
            "verify_skip_test_repair": True,
            "owner_attention": "pytest_execute_pre_repair: repository root unresolved",
        }
    module_paths = _module_paths_for_item(state, item)
    max_attempts = int(state.get("verify_pytest_harness_max_attempts") or 3)
    request = PytestExecuteRequest(
        scope_kind="MODULES" if module_paths else "FULL_SUITE",
        module_paths=module_paths,
        max_harness_attempts=max_attempts,
        collect_only=False,
    )
    result = await run_pytest_execute(
        repo_root,
        request,
        state=state,
        harness_repair_agent=harness_repair_agent,
        repair_adapter=repair_adapter,
    )
    patch: dict[str, Any] = {
        "pytest_execute_pre_repair_result": result.model_dump(mode="json"),
        **pytest_execute_state_patch(result),
    }
    if result.status == "PASS":
        patch["verify_skip_test_repair"] = False
        patch["verify_finding_repair_outcome"] = {
            "workflow": "pytest_execute_pre_repair",
            "status": "MODULE_PASS",
            "detail": result.detail,
        }
    elif result.status == "PAUSE":
        patch["verify_skip_test_repair"] = True
        patch["owner_attention"] = result.owner_attention or result.detail
    else:
        patch["verify_skip_test_repair"] = False
    return patch


def pytest_execute_pre_repair_route(state: GraphState) -> str:
    """После pre-repair execute: PAUSE → finalize; PASS/FAIL → test_repair (не закрывать finding)."""
    if state.get("verify_skip_test_repair"):
        return "test_finding_finalize"
    return "test_repair"
