"""Handoff при BLOCKED test_repair из‑за deny read_scope (outside_restrict_allow)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.config import ReadScopeViolation
from agents.state import GraphState

if TYPE_CHECKING:
    from agents.adapters.abc_adapter import AgentResult
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from agents.verify.group_error.gate_only_triage import (
    build_gate_only_group_error_result,
    deterministic_group_error_handoff_patch,
)

_SCOPE_PATH_RE = re.compile(
    r"(?:path='([^']+)'|path=\"([^\"]+)\"|"
    r"(tools/openspec/consultation_baseline/pinned/manifest\.json)|"
    r"(tools/openspec/consultation_baseline/pinned_sources\.manifest\.json))",
    re.IGNORECASE,
)


def normalize_scope_denial_path(path: str, repo_root: Path | None = None) -> str:
    """Привести path нарушения к относительному repo-пути с ``/`` для handoff."""
    raw = str(path or "").strip()
    if not raw:
        return raw
    normalized = raw.replace("\\\\", "/").replace("\\", "/")
    if repo_root is None:
        return normalized
    try:
        root = repo_root.resolve()
        candidate = Path(raw)
        if candidate.is_absolute():
            rel = candidate.resolve().relative_to(root)
            return rel.as_posix()
        root_posix = root.as_posix().rstrip("/")
        prefix = f"{root_posix}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    except (OSError, ValueError, TypeError):
        pass
    return normalized


def scope_denial_summary_from_violation(
    exc: ReadScopeViolation,
    *,
    repo_root: Path | None = None,
) -> str:
    """Текст BLOCKED, совместимый с ``parse_scope_denial_from_repair_text``."""
    path = normalize_scope_denial_path(exc.path, repo_root)
    rule = str(exc.rule or "outside_restrict_allow").strip() or "outside_restrict_allow"
    tool = str(exc.tool or "edit").strip() or "edit"
    return f"READ_SCOPE_VIOLATION tool={tool} path='{path}' rule='{rule}'"


def synthetic_blocked_test_repair_agent_result(
    exc: ReadScopeViolation,
    *,
    repo_root: Path | None = None,
) -> AgentResult:
    """Вернуть terminal ``AgentResult`` с BLOCKED JSON вместо contract error.

    Вызывается из ``ABCAgent.submit_turn`` после исчерпания единственной
    коррекции ``outside_restrict_allow`` на hop ``test_repair``.
    """
    from agents.adapters.abc_adapter import AgentResult

    summary = scope_denial_summary_from_violation(exc, repo_root=repo_root)
    payload = {
        "schema_version": "1.0",
        "agent": "test_repair",
        "finding_id": "scope-denied",
        "status": "BLOCKED",
        "summary": summary,
        "files_changed": [],
        "notes": summary,
    }
    return AgentResult(
        text=json.dumps(payload, ensure_ascii=False),
        status="finished",
        agent_id=str(exc.agent_id or ""),
        run_id=str(exc.run_id or ""),
    )


def is_test_repair_outside_allow_handoff(
    agent_key: str | None,
    exc: ReadScopeViolation,
) -> bool:
    """True, если нарушение должно стать REPAIR_SCOPE_DENIED, а не contract error."""
    if agent_key != "test_repair":
        return False
    return str(exc.rule or "").strip() == "outside_restrict_allow"


def parse_scope_denial_from_repair_text(
    *parts: str | None,
) -> tuple[str, str, str] | None:
    """Извлечь (path, rule, tool) из summary/notes BLOCKED test_repair."""
    blob = "\n".join(p for p in parts if p and str(p).strip())
    if not blob.strip():
        return None
    lower = blob.lower()
    if "outside_restrict_allow" not in lower and "read_scope_violation" not in lower:
        return None
    rule = "outside_restrict_allow"
    if "outside_restrict_allow" not in lower:
        rule_match = re.search(r"rule='([^']+)'", blob)
        if rule_match is not None:
            rule = rule_match.group(1)
    tool = "edit"
    tool_match = re.search(r"tool=([a-z_]+)", blob, re.IGNORECASE)
    if tool_match is not None:
        tool = tool_match.group(1)
    path = ""
    for match in _SCOPE_PATH_RE.finditer(blob):
        path = (
            match.group(1)
            or match.group(2)
            or match.group(3)
            or match.group(4)
            or ""
        )
        if path:
            break
    if not path:
        return None
    path = path.replace("\\\\", "/").replace("\\", "/")
    return path, rule, tool


def build_scope_denial_handoff_patch(
    state: GraphState,
    *,
    path: str,
    rule: str,
    tool: str,
) -> dict[str, Any]:
    """Обновить ``quality_gate_diagnostics`` и ``analysis_result`` для scope BLOCKED.

    Не помечает gates PASS и не снимает VERIFY_PAUSED сам по себе; даёт group_error
    детерминированный COMPLETE handoff с именованным deny path/rule.
    """
    raw = state.get("quality_gate_diagnostics")
    diag: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    denials_raw = diag.get("repair_scope_denials")
    denials: list[dict[str, str]] = []
    if isinstance(denials_raw, list):
        for item in denials_raw:
            if isinstance(item, dict):
                denials.append(
                    {
                        "hop": str(item.get("hop") or "test_repair"),
                        "path": str(item.get("path") or ""),
                        "rule": str(item.get("rule") or ""),
                        "tool": str(item.get("tool") or "edit"),
                    },
                )
    entry = {
        "hop": "test_repair",
        "path": path,
        "rule": rule,
        "tool": tool,
    }
    if entry not in denials:
        denials.append(entry)
    diag["repair_scope_denials"] = denials

    scope_note = (
        f"test_repair scope denial documented: tool={tool} path={path} rule={rule}"
    )
    prior_issues_raw = diag.get("input_integrity_issues")
    issues = (
        [str(x) for x in prior_issues_raw if str(x).strip()]
        if isinstance(prior_issues_raw, list)
        else []
    )
    if scope_note not in issues:
        issues.append(scope_note)
    diag["input_integrity_status"] = "COMPLETE"
    diag["input_integrity_issues"] = issues

    change_id = str(state.get("change_id") or "").strip()
    execution_id = state.get("quality_gates_run_id") or state.get("run_id")
    execution_key = str(execution_id) if execution_id else None
    integrity = input_integrity_from_gate_diagnostics(diag)
    analysis = build_gate_only_group_error_result(
        change_id=change_id,
        execution_id=execution_key,
        state={**state, "quality_gate_diagnostics": diag},
        input_integrity=integrity,
    )
    ge_patch = deterministic_group_error_handoff_patch(
        analysis,
        execution_status="deterministic_gate_only",
    )
    owner = (
        f"REPAIR_SCOPE_DENIED: hop=test_repair tool={tool} "
        f"path={path} rule={rule}; executor outside test_repair ALLOW must refresh pin"
    )
    result_patch: dict[str, Any] = {
        "quality_gate_diagnostics": diag,
        **ge_patch,
        "owner_attention": owner,
        "verify_finding_repair_outcome": {
            "workflow": "test_repair",
            "status": "BLOCKED",
            "scope_denial": entry,
        },
    }
    return result_patch


def scope_denial_tuple_from_violation(
    exc: ReadScopeViolation,
    *,
    repo_root: Path | None = None,
) -> tuple[str, str, str]:
    """Построить (path, rule, tool) из зафиксированного ``ReadScopeViolation`` hop."""
    path = normalize_scope_denial_path(exc.path, repo_root)
    rule = str(exc.rule or "outside_restrict_allow").strip() or "outside_restrict_allow"
    tool = str(exc.tool or "edit").strip() or "edit"
    return path, rule, tool


def maybe_scope_denial_handoff_from_repair(
    state: GraphState,
    *,
    status: str,
    summary: str,
    notes: str | None,
    recorded_violation: ReadScopeViolation | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Вернуть patch handoff при BLOCKED с outside_restrict_allow, иначе пустой dict.

    Сначала парсит summary/notes модели; при неудаче использует
    ``recorded_violation`` с hop (guard exception, soft MCP tool-result или коррекция).
    """
    if status != "BLOCKED":
        return {}
    parsed = parse_scope_denial_from_repair_text(summary, notes)
    if parsed is not None:
        path, rule, tool = parsed
    elif (
        recorded_violation is not None
        and is_test_repair_outside_allow_handoff("test_repair", recorded_violation)
    ):
        path, rule, tool = scope_denial_tuple_from_violation(
            recorded_violation,
            repo_root=repo_root,
        )
    else:
        return {}
    return build_scope_denial_handoff_patch(state, path=path, rule=rule, tool=tool)
