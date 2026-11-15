"""Post-repair verify findings группы через **свежие** focused quality gates."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.fresh_focused_gates import (
    collect_fresh_runs,
    observation_matches_fresh_issue,
    run_fresh_gate_for_tool,
    resolve_repo_root,
    _infer_tool,
)
from agents.verify.dispatch_unit.schemas import FindingMemberStatus
from agents.verify.group_error.schemas import GroupErrorResult, Finding
from agents.verify.pytest_run_health import (
    affected_test_paths,
    is_harness_blocker,
    scope_key_from_modules,
)


def _harness_collect_resolved(
    state: GraphState,
    finding: Finding,
) -> FindingMemberStatus | None:
    """RESOLVED/UNRESOLVED по ``last_pytest_execute_by_scope`` без full-suite."""
    cache = state.get("last_pytest_execute_by_scope")
    if not isinstance(cache, dict):
        return FindingMemberStatus(
            finding_id=finding.finding_id,
            status="UNKNOWN",
            detail="no pytest_execute scope cache for harness finding",
        )
    modules = affected_test_paths(finding.observation or "")
    key = scope_key_from_modules(modules)
    entry = cache.get(key)
    if not isinstance(entry, dict):
        for cached_key, cached_val in cache.items():
            if isinstance(cached_val, dict) and cached_val.get("collect_ok"):
                entry = cached_val
                key = str(cached_key)
                break
    if not isinstance(entry, dict):
        return FindingMemberStatus(
            finding_id=finding.finding_id,
            status="UNKNOWN",
            detail=f"no collect_proof for scope {key!r}",
        )
    if not entry.get("collect_ok"):
        return FindingMemberStatus(
            finding_id=finding.finding_id,
            status="UNRESOLVED",
            detail=f"collect still failing ({entry.get('collect_exit_code')})",
        )
    tail = str(entry.get("collect_tail") or "")
    obs = (finding.observation or "").strip()
    if obs and obs in tail:
        return FindingMemberStatus(
            finding_id=finding.finding_id,
            status="UNRESOLVED",
            detail="harness observation still in collect tail",
        )
    return FindingMemberStatus(
        finding_id=finding.finding_id,
        status="RESOLVED",
        detail=f"collect_proof PASS for scope {key}",
    )


def _findings_map(state: GraphState) -> dict[str, Finding]:
    raw = state.get("analysis_result")
    if not isinstance(raw, dict):
        return {}
    analysis = GroupErrorResult.model_validate(raw)
    return {f.finding_id: f for f in analysis.findings}


def verify_group_findings(
    state: GraphState,
    *,
    finding_ids: list[str],
    files_changed: list[str],
    pytest_modules: list[str] | None = None,
) -> list[FindingMemberStatus]:
    """Оценить статус каждого finding после repair hop.

    **Только** свежий subprocess pytest/pyright/mypy (focused). Pre-repair snapshot
    **не** используется для RESOLVED. Без успешного fresh run → ``UNKNOWN``.
    ``RESOLVED`` / ``ALREADY_RESOLVED`` только если observation не найдена в свежем выводе.
    """
    findings_map = _findings_map(state)
    repo_raw = str(state.get("repository_path") or state.get("workspace") or ".")
    repo_root = resolve_repo_root(repo_raw)
    extra_paths = {p for p in files_changed if p}

    group_findings: list[Finding] = []
    for fid in finding_ids:
        f = findings_map.get(fid)
        if f is not None:
            group_findings.append(f)

    fresh_by_tool: dict[str, Any] = {}
    if repo_root is not None and group_findings:
        fresh_by_tool = collect_fresh_runs(
            repo_root,
            group_findings,
            extra_paths=extra_paths,
            pytest_modules=pytest_modules,
        )

    out: list[FindingMemberStatus] = []
    for fid in finding_ids:
        finding = findings_map.get(fid)
        if finding is None:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNKNOWN",
                    detail="finding missing from analysis_result",
                ),
            )
            continue
        if is_harness_blocker(finding.repair_workflow):
            harness_status = _harness_collect_resolved(state, finding)
            if harness_status is not None:
                out.append(harness_status)
                continue
        tool = _infer_tool(finding)
        fresh = fresh_by_tool.get(tool)
        if repo_root is None:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNKNOWN",
                    detail="OPSX repository root unavailable; fresh gate not run",
                ),
            )
            continue
        if fresh is None:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNKNOWN",
                    detail=f"no fresh run scheduled for tool {tool}",
                ),
            )
            continue
        if not fresh.ok:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNKNOWN",
                    detail=f"fresh {tool} run failed: {fresh.detail}",
                ),
            )
            continue
        match = observation_matches_fresh_issue(finding, fresh.issues)
        if match is True:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNRESOLVED",
                    detail=f"observation still present in fresh {tool} output",
                ),
            )
            continue
        if match is None:
            out.append(
                FindingMemberStatus(
                    finding_id=fid,
                    status="UNKNOWN",
                    detail=f"cannot match observation to fresh {tool} output reliably",
                ),
            )
            continue
        # A passing cohort can conceal suite-order dependencies. If the
        # agent made no edits, prove absence under the original full-suite
        # boundary before accepting ALREADY_RESOLVED/RESOLVED.
        if tool == "pytest" and not files_changed and fresh.exit_code == 0:
            full = fresh_by_tool.get("pytest_full")
            if full is None:
                try:
                    full = run_fresh_gate_for_tool(
                        repo_root, "pytest", rel_paths=set(), full_suite=True,
                    )
                except (OSError, ValueError) as exc:
                    from agents.verify.dispatch_unit.fresh_focused_gates import FreshGateRun
                    full = FreshGateRun(
                        tool="pytest", ok=False, issues=[],
                        detail=f"full-suite gate unavailable: {type(exc).__name__}: {exc}",
                    )
                fresh_by_tool["pytest_full"] = full
            if not full.ok:
                status = "UNKNOWN"
                reason = f"full pytest gate unavailable: {full.detail}"
            elif observation_matches_fresh_issue(finding, full.issues):
                status = "UNRESOLVED"
                reason = "cohort passes in isolation but same finding fails in full pytest"
            elif full.exit_code != 0:
                status = "UNKNOWN"
                reason = "cohort passes, full pytest still fails; re-triage full gate"
            else:
                status = "RESOLVED"
                reason = f"full pytest confirms absence of finding ({full.detail})"
            out.append(FindingMemberStatus(
                finding_id=fid, status=status, detail=reason,
            ))
            continue
        if tool == "pytest" and fresh.exit_code != 0:
            out.append(FindingMemberStatus(
                finding_id=fid,
                status="UNKNOWN",
                detail=f"other errors in cohort prevent a supported closure: {fresh.detail}",
            ))
            continue
        out.append(
            FindingMemberStatus(
                finding_id=fid,
                status="RESOLVED",
                detail=f"observation absent from fresh {tool} run ({fresh.detail})",
            ),
        )
    return out


def verify_findings_already_absent(
    state: GraphState,
    *,
    finding_ids: list[str],
    pytest_modules: list[str] | None = None,
) -> list[FindingMemberStatus]:
    """Pre-repair check: ``ALREADY_RESOLVED`` только при успешном fresh gate."""
    statuses = verify_group_findings(
        state, finding_ids=finding_ids, files_changed=[],
        pytest_modules=pytest_modules,
    )
    mapped: list[FindingMemberStatus] = []
    for s in statuses:
        if s.status == "RESOLVED":
            mapped.append(
                FindingMemberStatus(
                    finding_id=s.finding_id,
                    status="ALREADY_RESOLVED",
                    detail=s.detail,
                ),
            )
        else:
            mapped.append(s)
    return mapped
