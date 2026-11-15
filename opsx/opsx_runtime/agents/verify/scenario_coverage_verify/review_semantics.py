"""Semantic source of truth for scenario model reviews and deterministic proof acceptance.

Only reviewer policy lives here; queueing, receipts, transport and restart logic
must not alter this file merely to change orchestration.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import re
from pathlib import Path
from typing import Any, Mapping

from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageRowReview, ScenarioCoverageVerifyAnswer,
)

_SCENARIO_HEADING = re.compile(r"^(#{1,6})\s+Scenario\s*[:：]\s*(.+?)\s*$", re.I)
_CLAUSE = re.compile(r"^\s*-\s*\*\*(WHEN|THEN|AND)\*\*\s*(.*?)\s*$", re.I)

def spec_clauses(
    repo_root: Path, change_id: str, scenario_id: str,
) -> tuple[list[str], list[str]]:
    """Read exact normative WHEN, THEN, AND bullets from one frozen spec scenario."""
    if scenario_id.startswith("DERIVED::"):
        # Plan-only derived rows (P0 characterization): proofs bind to tests, не к spec.
        return [], []
    parts = scenario_id.split("::", 1)
    if len(parts) != 2 or not parts[1].strip():
        raise ValueError(f"SCENARIO_SPEC_ID_INVALID: {scenario_id}")
    rel, title = parts
    root = repo_root.resolve()
    path = (resource_path(root, rel)).resolve()
    scope = (resource_path(root, "openspec", "changes", change_id, "specs")).resolve()
    if not path.is_relative_to(scope) or not path.is_file():
        raise ValueError(f"SCENARIO_SPEC_MISSING: {scenario_id}")
    lines = path.read_text(encoding="utf-8").splitlines()
    positions = [
        (i, m.group(1), m.group(2).strip())
        for i, line in enumerate(lines)
        if (m := _SCENARIO_HEADING.match(line.strip()))
        and m.group(2).strip() == title
    ]
    if len(positions) != 1:
        raise ValueError(f"SCENARIO_SPEC_AMBIGUOUS: {scenario_id}")
    pos, marks, _ = positions[0]
    start = pos + 1
    end = len(lines)
    for i in range(start, len(lines)):
        heading = re.match(r"^(#{1,6})\s", lines[i])
        if heading and len(heading.group(1)) <= len(marks):
            end = i
            break
    when: list[str] = []
    outcome: list[str] = []
    phase = "WHEN"
    for line in lines[start:end]:
        m = _CLAUSE.match(line)
        if not m:
            continue
        tag = m.group(1).upper()
        if tag in ("WHEN", "THEN"):
            phase = tag
        (when if phase == "WHEN" else outcome).append(m.group(2).strip())
    if not when or not outcome:
        raise ValueError(f"SCENARIO_SPEC_CLAUSES_MISSING: {scenario_id}")
    return when, outcome


def _is_then_and_assert_line(line: str) -> bool:
    """Строка mapped test подходит как позиционный THEN/AND proof."""
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", '"""', "'''")):
        return False
    return bool(
        stripped.startswith(("assert ", "assert(", "assert_", "await assert_"))
        or "pytest.raises(" in stripped
        or ".assert_" in stripped
        or stripped.startswith("with raises(")
    )


def _valid_then_and_assert_lines(lines: list[str], first: int, last: int) -> list[int]:
    """Абсолютные номера строк тела test-функции с допустимым assert для THEN/AND."""
    return [
        line_no
        for line_no in range(first, last + 1)
        if _is_then_and_assert_line(lines[line_no - 1])
    ]


def _enrich_proof_error_message(
    message: str,
    lines: list[str] | None,
    first: int | None,
    last: int | None,
) -> str:
    if not lines or first is None or last is None:
        return message
    if "valid_then_and_assert_lines=" in message:
        return message
    valid = _valid_then_and_assert_lines(lines, first, last)
    if not valid:
        return message
    joined = ",".join(str(line_no) for line_no in valid)
    return f"{message}; valid_then_and_assert_lines={joined}"


def _test_function_lines(path: Path, node_id: str) -> tuple[list[str], int, int]:
    name = node_id.split("::", 1)[1].split("::")[-1].split("[", 1)[0]
    source = path.read_text(encoding="utf-8")
    try:
        _ = compile(source, str(path), "exec")
    except SyntaxError as exc:
        raise SyntaxError(
            f"MAPPED_TEST_SYNTAX_ERROR: {exc.msg} (line {exc.lineno})"
        ) from exc
    body = source.splitlines()
    parsed = ast.parse(source, filename=str(path))
    functions = [
        n for n in ast.walk(parsed)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
    ]
    if len(functions) != 1:
        raise ValueError(f"TEST_NODE_NOT_FOUND: {node_id}")
    fn = functions[0]
    return body, fn.lineno, fn.end_lineno or fn.lineno


def review_proof_error(
    repo_root: Path,
    change_id: str,
    row: Any,
    *,
    expected_node: str | None,
) -> str | None:
    """Отклонить SUPPORTED без проверяемых source ref в mapped test.

    В тексте ошибки proof для retry добавляет ``valid_then_and_assert_lines`` —
    абсолютные номера строк тела test-функции, уже удовлетворяющие правилу
    assert для позиционных THEN/AND (если список ещё не присутствует).
    """
    lines: list[str] | None = None
    first: int | None = None
    last: int | None = None
    try:
        when, outcome = spec_clauses(repo_root, change_id, row.scenario_id)
        if not expected_node or row.test_node_id != expected_node:
            raise ValueError(f"MAPPED_TEST_MISMATCH: {row.scenario_id}")
        rel = expected_node.split("::", 1)[0]
        root = repo_root.resolve()
        path = (resource_path(root, rel)).resolve()
        if not path.is_relative_to((resource_path(root, "tests")).resolve()) or not path.is_file():
            raise ValueError(f"MAPPED_TEST_MISSING: {expected_node}")
        lines, first, last = _test_function_lines(path, expected_node)
        if str(row.scenario_id).startswith("DERIVED::"):
            for n in list(row.when_proofs) + list(row.outcome_proofs):
                if not first <= n <= last or not 1 <= n <= len(lines):
                    raise ValueError(f"PROOF_OUTSIDE_TEST: {expected_node}:{n}")
                line = lines[n - 1].strip()
                if not line or line.startswith(("#", '"""', "'''")):
                    raise ValueError(f"PROOF_SOURCE_MISMATCH: {expected_node}:{n}")
            return None
        for name, clauses, proofs in (
            ("WHEN", when, row.when_proofs),
            ("THEN_AND", outcome, row.outcome_proofs),
        ):
            # Proofs are positional: one line number per frozen clause.
            if len(proofs) != len(clauses):
                raise ValueError(
                    f"PROOF_CLAUSES_MISMATCH: {row.scenario_id} {name}; "
                    f"expected={len(clauses)}, got={len(proofs)}"
                )
            if not clauses and not proofs:
                continue
            for n in proofs:
                if not first <= n <= last or not 1 <= n <= len(lines):
                    raise ValueError(f"PROOF_OUTSIDE_TEST: {expected_node}:{n}")
                line = lines[n - 1].strip()
                if not line or line.startswith(("#", '"""', "'''")):
                    raise ValueError(f"PROOF_SOURCE_MISMATCH: {expected_node}:{n}")
                if name == "THEN_AND" and not _is_then_and_assert_line(lines[n - 1]):
                    raise ValueError(f"PROOF_NOT_ASSERTION: {expected_node}:{n}")
    except (ValueError, SyntaxError, OSError) as exc:
        return _enrich_proof_error_message(str(exc), lines, first, last)
    return None


def trace_mappings(repo_root: Path, change_id: str) -> dict[str, str]:
    import json

    from workflows.artifact_paths import scenario_machine_receipt_path

    path = scenario_machine_receipt_path(repo_root, change_id, "test_trace_matrix.json")
    obj = json.loads(path.read_text(encoding="utf-8"))
    entries = obj.get("entries")
    if not isinstance(entries, list):
        raise ValueError("TRACE_MATRIX_INVALID: entries missing")
    mapping: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("TRACE_MATRIX_INVALID: entry is not an object")
        sid, node = entry.get("scenario_id"), entry.get("test_node_id")
        if not isinstance(sid, str) or not sid:
            raise ValueError("TRACE_MATRIX_INVALID: scenario_id required")
        if not isinstance(node, str):
            raise ValueError("TRACE_MATRIX_INVALID: test_node_id must be a string")
        if sid in mapping:
            raise ValueError(f"TRACE_MATRIX_DUPLICATE: {sid}")
        mapping[sid] = node.strip()
    return mapping

def verify_single_answer(
    *,
    answer: ScenarioCoverageVerifyAnswer,
    scenario_id: str,
    mapped_node: str,
    plan_id: str,
    source_digest: str,
) -> ScenarioCoverageRowReview:
    """Single-result row status and mapping must be unambiguous (plan/digest bound by hop)."""
    del plan_id, source_digest  # machine-bound before this call; not re-validated from model echo
    if len(answer.row_reviews) != 1:
        raise ValueError("SCENARIO_ROW_NOT_EXACTLY_ONE")
    row = answer.row_reviews[0]
    if row.scenario_id != scenario_id:
        raise ValueError("SCENARIO_ROW_ID_MISMATCH")
    if row.assessment == "SUPPORTED":
        if (
            row.test_node_id != mapped_node
            or answer.verdict != "SCENARIO_TEST_COVERAGE_PASS"
            or answer.gaps
        ):
            raise ValueError("SCENARIO_ROW_FALSE_PASS")
    elif row.assessment == "GAP":
        if (
            answer.verdict != "SCENARIO_TEST_COVERAGE_FAIL"
            or not answer.gaps
            or any(g.scenario_id != scenario_id for g in answer.gaps)
        ):
            raise ValueError("SCENARIO_ROW_GAP_MISMATCH")
    else:
        if answer.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" or not answer.block_reason:
            raise ValueError("SCENARIO_ROW_BLOCKED_MUST_BE_UNKNOWN")
    if row.test_node_id and row.test_node_id != mapped_node:
        raise ValueError("SCENARIO_ROW_MAPPING_CHANGED")
    return row
