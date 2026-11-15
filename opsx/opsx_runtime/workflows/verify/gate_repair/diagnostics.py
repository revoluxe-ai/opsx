"""Детерминированный разбор логов gate-инструментов для handoff (без quality_gates agent)."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.verify.test_duration.timeout_policy import stall_evidence_from_gate_error

from workflows.verify.gate_repair.input_integrity import (
    assess_pytest_input_integrity,
    is_pytest_structured_gate_evidence,
    merge_input_integrity_status,
    promote_integrity_when_pytest_nodes_present,
)
from workflows.verify.gate_repair.mypy_output import (
    assess_mypy_input_integrity,
    parse_mypy_issues,
    parse_mypy_reported_error_count,
)
from workflows.verify.gate_repair.pytest_failure_excerpt import (
    build_pytest_failure_excerpts,
    node_id_from_pytest_observation,
)
from workflows.verify.gate_repair.pytest_opsx_gate_progress_parse import (
    iter_opsx_gate_segments,
)
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle
from workflows.verify.gate_repair.static_scope import gate_issue_in_product_static_scope

ToolId = Literal["pytest", "pyright", "mypy"]

_PYRIGHT_ERROR = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+)\s+-\s+error:\s+(?P<message>.+)$",
)
_PYTEST_FAILED = re.compile(r"^FAILED\s+(?P<nodeid>.+?)\s+-\s*(?P<reason>.*)$")
_PYTEST_ERROR = re.compile(r"^ERROR\s+(?P<nodeid>.+?)\s+-\s*(?P<reason>.*)$")
_PYTEST_PREFIXED = re.compile(
    r"^pytest:\s*(?P<kind>FAILED|ERROR)\s+(?P<nodeid>.+?)\s+-\s*(?P<reason>.*)$",
)
_PYTEST_LINE_ERROR = re.compile(r"^E\s+(?P<message>.+)$")
_PYTEST_NODE_IN_LINE = re.compile(
    r"(?:^|\s)(?P<nodeid>(?:tests|src)[^\s]+?\.py(?:::[^\s]+)?)",
)


class QualityGateDiagnostics(BaseModel):
    """Списки проблем по инструментам (machine-parsed) для group_error / receipt."""

    model_config = ConfigDict(extra="forbid")

    pytest: list[str] = Field(default_factory=list)
    pytest_failure_excerpts: dict[str, str] = Field(default_factory=dict)
    pytest_stall_evidence_by_node: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pyright: list[str] = Field(default_factory=list)
    mypy: list[str] = Field(default_factory=list)
    mypy_reported_error_count: int | None = None
    input_integrity_status: Literal["COMPLETE", "PARTIAL", "INVALID"] = "PARTIAL"
    input_integrity_issues: list[str] = Field(default_factory=list)
    repair_scope_denials: list[dict[str, str]] = Field(
        default_factory=list,
        description="BLOCKED repair hops: path/rule вне restrict_allow (machine handoff).",
    )
    repair_execution_blocked: list[dict[str, str]] = Field(
        default_factory=list,
        description="BLOCKED IMPLEMENTATION_EXECUTOR: named_test/hop/reason без shell.",
    )

    @property
    def total_count(self) -> int:
        return len(self.pytest) + len(self.pyright) + len(self.mypy)

    def as_handoff_dict(self) -> dict[str, list[str] | int | str | dict[str, str] | None]:
        """JSON-ready структура для ``quality_gate_diagnostics`` в GraphState."""
        parsed_mypy = len(self.mypy)
        handoff: dict[str, list[str] | int | str | dict[str, str] | None] = {
            "pytest": list(self.pytest),
            "pytest_failure_excerpts": dict(self.pytest_failure_excerpts),
            "pytest_stall_evidence_by_node": {
                key: dict(value) for key, value in self.pytest_stall_evidence_by_node.items()
            },
            "pyright": list(self.pyright),
            "mypy": list(self.mypy),
            "total_count": self.total_count,
            "mypy_parsed_issue_count": parsed_mypy,
            "mypy_reported_error_count": self.mypy_reported_error_count,
            "input_integrity_status": self.input_integrity_status,
            "input_integrity_issues": list(self.input_integrity_issues),
            "repair_scope_denials": [dict(item) for item in self.repair_scope_denials],
            "repair_execution_blocked": [
                dict(item) for item in self.repair_execution_blocked
            ],
        }
        reported = self.mypy_reported_error_count
        if reported is not None and reported != parsed_mypy:
            handoff["mypy_count_mismatch_note"] = (
                "mypy_reported_error_count — из summary-строки mypy; "
                "mypy_parsed_issue_count — число распарсенных error-строк. "
                "При input_integrity_status=PARTIAL repair cycle блокируется."
            )
        return handoff


def build_pytest_stall_evidence_by_node(error: str | None) -> dict[str, dict[str, Any]]:
    """Словарь node id → stall JSON из structured ``GateCheckResult.error``."""
    payload = stall_evidence_from_gate_error(error)
    if not payload:
        return {}
    raw_node = str(payload.get("last_node_id") or "").strip()
    node = _normalize_pytest_node_id(raw_node)
    if node is None:
        return {}
    return {node: dict(payload)}


def lookup_pytest_stall_evidence(
    diagnostics: dict[str, Any] | None,
    *,
    observation: str,
) -> dict[str, Any] | None:
    """Взять stall evidence для node из observation."""
    if not diagnostics:
        return None
    raw_map = diagnostics.get("pytest_stall_evidence_by_node")
    if not isinstance(raw_map, dict):
        return None
    node = node_id_from_pytest_observation(observation)
    if node is None:
        return None
    row = raw_map.get(node)
    return dict(row) if isinstance(row, dict) else None


def _log_text(check: GateCheckResult) -> str:
    parts = [check.stdout_tail, check.stderr_tail]
    return "\n".join(part for part in parts if part)


def parse_pyright_issues(text: str) -> list[str]:
    """Строки ``path:line:col - error: …`` из вывода pyright."""
    issues: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _PYRIGHT_ERROR.match(stripped)
        if match:
            formatted = (
                f"{match.group('path')}:{match.group('line')}: "
                f"{match.group('message').strip()}"
            )
        elif " - error:" in stripped and ":" in stripped:
            formatted = stripped
        else:
            continue
        if formatted not in seen:
            seen.add(formatted)
            issues.append(formatted)
    return issues


def _normalize_pytest_node_id(raw: str) -> str | None:
    """Оставить только node id, если в строке есть ``.py`` (pytest item id)."""
    node = raw.strip().split(" - ", 1)[0].strip()
    if node == "UNKNOWN_NODE":
        return node
    if ".py" not in node:
        return None
    return node.replace("\\", "/")


def _format_pytest_observation(nodeid: str, *, reason: str | None = None) -> str:
    """Единый префикс ``pytest:`` для ``pytest_node_id()`` в repro_cohort."""
    nodeid = nodeid.replace("\\", "/")
    if reason and reason.strip():
        short = reason.strip()
        if len(short) > 240:
            short = short[:237] + "..."
        return f"pytest: {nodeid} - {short}"
    return f"pytest: {nodeid}"


def _parse_pytest_summary_line(stripped: str) -> tuple[str, str | None] | None:
    """Извлечь node id и reason из строки short test summary."""
    for pattern in (_PYTEST_PREFIXED, _PYTEST_FAILED, _PYTEST_ERROR):
        match = pattern.match(stripped)
        if match is None:
            continue
        nodeid = _normalize_pytest_node_id(match.group("nodeid"))
        if nodeid is None:
            continue
        reason = match.group("reason").strip() or None
        return nodeid, reason
    return None


def _scan_pytest_summary_nodes(text: str) -> dict[str, str | None]:
    """Все node id из FAILED/ERROR summary по всему логу (dedup по node)."""
    by_node: dict[str, str | None] = {}
    for line in text.splitlines():
        parsed = _parse_pytest_summary_line(line.strip())
        if parsed is None:
            continue
        nodeid, reason = parsed
        if nodeid not in by_node or (reason and not by_node[nodeid]):
            by_node[nodeid] = reason
    return by_node


def parse_pytest_issues(text: str) -> list[str]:
    """FAILED/ERROR node ids из вывода pytest; ``E`` привязываются к текущему node."""
    by_node: dict[str, str] = {}
    current_node: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            current_node = None
            continue

        parsed = _parse_pytest_summary_line(stripped)
        if parsed is not None:
            nodeid, reason = parsed
            current_node = nodeid
            obs = _format_pytest_observation(nodeid, reason=reason)
            prev = by_node.get(nodeid)
            if prev is None or (reason and " - " not in prev[len(f"pytest: {nodeid}") :]):
                by_node[nodeid] = obs
            continue

        em = _PYTEST_LINE_ERROR.match(stripped)
        if em is not None:
            if current_node is not None:
                msg = em.group("message").strip()
                obs = _format_pytest_observation(current_node, reason=msg)
                if current_node not in by_node or " - " not in by_node[current_node]:
                    by_node[current_node] = obs
            continue

        node_match = _PYTEST_NODE_IN_LINE.search(stripped)
        if node_match is not None and ("FAILED" in stripped or "ERROR" in stripped):
            nodeid = _normalize_pytest_node_id(node_match.group("nodeid"))
            if nodeid is not None:
                current_node = nodeid
                by_node.setdefault(nodeid, _format_pytest_observation(nodeid))

    for nodeid, reason in _scan_pytest_summary_nodes(text).items():
        obs = _format_pytest_observation(nodeid, reason=reason)
        prev = by_node.get(nodeid)
        if prev is None:
            by_node[nodeid] = obs
        elif reason and " - " not in prev:
            by_node[nodeid] = obs

    return list(by_node.values())


def last_pytest_node_id_from_progress_text(text: str) -> str | None:
    """Последний именованный node id из прогресса, если short summary в логе отсутствует.

    Покрывает visual agent-20261113-111943: subprocess убит по call-phase, в stdout
    только OPSX_GATE_PROGRESS / ``-v`` без строки ``FAILED … - PYTEST_STALL``.
    """
    open_call_node: str | None = None
    last_node: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        segments = list(iter_opsx_gate_segments(line))
        if segments:
            for gate in segments:
                node = _normalize_pytest_node_id(str(gate.get("node") or ""))
                if node is None or node == "UNKNOWN_NODE":
                    continue
                when = str(gate.get("when") or "")
                event = str(gate.get("event") or "")
                last_node = node
                if when == "call" and event == "start":
                    open_call_node = node
                elif when == "call" and event == "report" and open_call_node == node:
                    open_call_node = None
            continue
        for match in _PYTEST_NODE_IN_LINE.finditer(stripped):
            node = _normalize_pytest_node_id(match.group("nodeid"))
            if node is not None and node != "UNKNOWN_NODE":
                last_node = node
    target = open_call_node or last_node
    return target


def resolve_stall_last_node_id(last_node_id: str, progress_text: str) -> str:
    """Node id для stall/suite evidence: не оставлять ``UNKNOWN_NODE``, если в логе есть прогресс."""
    normalized = _normalize_pytest_node_id(str(last_node_id or "").strip())
    if normalized and normalized != "UNKNOWN_NODE":
        return normalized
    resolved = last_pytest_node_id_from_progress_text(progress_text)
    if resolved:
        return resolved
    raw = str(last_node_id or "").strip()
    return raw or "UNKNOWN_NODE"


def pytest_stall_observation_from_progress_log(text: str) -> list[str]:
    """Синтезировать stall-finding из прогресса без ``GateCheckResult.error``."""
    node = last_pytest_node_id_from_progress_text(text)
    if node is None:
        return []
    return [_format_pytest_observation(node, reason="PYTEST_STALL")]


def _fallback_pytest_issues(text: str) -> list[str]:
    """Последний уровень: summary nodes, stall по прогрессу или collection failed."""
    nodes = _scan_pytest_summary_nodes(text)
    if nodes:
        return [_format_pytest_observation(n, reason=r) for n, r in sorted(nodes.items())]
    progress_stall = pytest_stall_observation_from_progress_log(text)
    if progress_stall:
        return progress_stall
    stripped = text.strip()
    if stripped:
        return ["pytest: collection failed"]
    return []


_COLLECTION_FAILED_OBS = "pytest: collection failed"


def _pytest_issues_are_collection_placeholder(issues: list[str]) -> bool:
    """True, если handoff pytest-список — только fallback без node id."""
    if not issues:
        return False
    normalized = {item.strip().lower() for item in issues if item.strip()}
    if len(normalized) != 1:
        return False
    return _COLLECTION_FAILED_OBS in normalized


def pytest_observations_from_structured_gate_error(
    error: str | None,
) -> list[str]:
    """Именованный node id из JSON stall/suite evidence ``GateCheckResult.error``.

    Используется, когда subprocess убит по call-phase / stall, а хвост stdout
    не содержит ``FAILED …`` (прогресс pytest-only, огромный лог).
    """
    if not is_pytest_structured_gate_evidence(error):
        return []
    try:
        payload = json.loads(str(error))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    raw_node = str(payload.get("last_node_id") or "").strip()
    tail = str(payload.get("stdout_tail") or "")
    effective = resolve_stall_last_node_id(raw_node, tail)
    node = _normalize_pytest_node_id(effective)
    if node is None or node == "UNKNOWN_NODE":
        return []
    if "stall_limit_s" in payload or payload.get("stall_when"):
        reason = "PYTEST_STALL"
    elif "timeout_s" in payload:
        reason = "PYTEST_SUITE_TIMEOUT"
    else:
        reason = "PYTEST_STALL"
    return [_format_pytest_observation(node, reason=reason)]


def _finalize_pytest_issues_for_failed_gate(
    check: GateCheckResult,
    parsed_issues: list[str],
    pytest_text: str,
) -> list[str]:
    """Свести parse log, structured evidence и collection-fallback."""
    if check.exit_code == 0:
        return parsed_issues
    structured = pytest_observations_from_structured_gate_error(check.error)
    if structured:
        return structured
    if parsed_issues and not _pytest_issues_are_collection_placeholder(parsed_issues):
        return parsed_issues
    progress_stall = pytest_stall_observation_from_progress_log(pytest_text)
    if progress_stall:
        return progress_stall
    if parsed_issues:
        return parsed_issues
    return _fallback_pytest_issues(pytest_text)


def extract_gate_diagnostics(
    bundle: QualityGateRawBundle,
    *,
    mypy_parse_log: str | None = None,
    pytest_parse_log: str | None = None,
) -> QualityGateDiagnostics:
    """Собрать списки ошибок из bundle.

    Args:
        bundle: Subprocess metadata и log tails для persistence.
        mypy_parse_log: Полный stdout+stderr mypy для парсинга (не сохраняется в state).
        pytest_parse_log: Полный лог pytest run при наличии (``pytest_execute``).
    """
    pytest_text = pytest_parse_log if pytest_parse_log is not None else _log_text(bundle.pytest)
    pyright_text = _log_text(bundle.pyright)
    mypy_text = mypy_parse_log if mypy_parse_log is not None else _log_text(bundle.mypy)
    pytest_issues = _finalize_pytest_issues_for_failed_gate(
        bundle.pytest,
        parse_pytest_issues(pytest_text),
        pytest_text,
    )
    pyright_issues = [
        line
        for line in parse_pyright_issues(pyright_text)
        if gate_issue_in_product_static_scope(line)
    ]
    mypy_issues_raw = parse_mypy_issues(mypy_text)
    mypy_issues = [
        line for line in mypy_issues_raw if gate_issue_in_product_static_scope(line)
    ]
    mypy_reported = parse_mypy_reported_error_count(mypy_text)

    pytest_nodes: list[str] = []
    for obs in pytest_issues:
        node = node_id_from_pytest_observation(obs)
        if node is not None and node not in pytest_nodes:
            pytest_nodes.append(node)
    pytest_excerpts = build_pytest_failure_excerpts(pytest_text, pytest_nodes)
    if bundle.pyright.exit_code != 0 and not pyright_issues:
        pyright_issues = [
            line.strip()
            for line in pyright_text.splitlines()
            if "error" in line.lower() and gate_issue_in_product_static_scope(line.strip())
        ][:500]
    if bundle.mypy.exit_code != 0 and not mypy_issues:
        mypy_issues = [
            line.strip()
            for line in mypy_text.splitlines()
            if "error" in line.lower() and gate_issue_in_product_static_scope(line.strip())
        ][:500]

    mypy_integrity_status, mypy_integrity_issues = assess_mypy_input_integrity(
        bundle.mypy,
        parsed_count=len(mypy_issues),
        reported=mypy_reported,
        log_text=mypy_text,
        raw_parsed_count=len(mypy_issues_raw),
    )
    pytest_integrity_status, pytest_integrity_issues = assess_pytest_input_integrity(
        bundle.pytest,
        parsed_count=len(pytest_issues),
        log_text=pytest_text,
    )
    integrity_status, integrity_issues = merge_input_integrity_status(
        (mypy_integrity_status, mypy_integrity_issues),
        (pytest_integrity_status, pytest_integrity_issues),
    )
    integrity_status, integrity_issues = promote_integrity_when_pytest_nodes_present(
        integrity_status,
        integrity_issues,
        pytest_exit_nonzero=bundle.pytest.exit_code != 0,
        pytest_node_count=len(pytest_nodes),
    )

    stall_by_node = build_pytest_stall_evidence_by_node(bundle.pytest.error)

    return QualityGateDiagnostics(
        pytest=pytest_issues,
        pytest_failure_excerpts=pytest_excerpts,
        pytest_stall_evidence_by_node=stall_by_node,
        pyright=pyright_issues,
        mypy=mypy_issues,
        mypy_reported_error_count=mypy_reported,
        input_integrity_status=integrity_status,
        input_integrity_issues=integrity_issues,
    )


# Alias для совместимости smoke-скриптов
extract_quality_gate_diagnostics = extract_gate_diagnostics


def resolve_quality_gate_verdict(
    *,
    pytest: GateCheckResult,
    diagnostics: QualityGateDiagnostics,
) -> str:
    """PASS, если pytest зелёный и нет in-scope находок typing/static gates.

    Pyright/mypy могут вернуть ненулевой ``exit_code`` при ошибках вне product scope
    (``opsx/``, ``tests/`` и т.п.). Такие провалы не попадают в diagnostics и не
    должны блокировать verify (иначе ``group_error`` не строит repair unit).
    """
    if diagnostics.input_integrity_status != "COMPLETE":
        return "FAIL"
    if pytest.exit_code != 0:
        return "FAIL"
    if diagnostics.total_count > 0:
        return "FAIL"
    return "PASS"
