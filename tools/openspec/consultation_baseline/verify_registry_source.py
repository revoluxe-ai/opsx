#!/usr/bin/env python3
"""Независимая верификация transition registry против source JSON @ BASE_SHA.

Назначение: отдельный алгоритм (tasks 1.7–1.9) — ожидаемые state/transition ID
строятся **только** из raw bytes контракта через documented byte pin
(``load_pinned_bytes`` / ``pinned/manifest.json``), без чтения
``manifest.transition_ids`` как oracle expected set. Сравнение с jsonl/manifest
выполняется для проверки артефактов генератора.

Вызывается: apply tasks 1.7–1.10; CI / evidence gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.openspec.consultation_baseline._constants import (
    JSONL_FILENAME,
    MANIFEST_FILENAME,
    evidence_dir,
    repo_root_from_here,
)
from tools.openspec.consultation_baseline._registry_build import (
    expected_transition_ids,
    json_pointer_for,
    state_ids_from_contract,
    transition_id_for,
)
from tools.openspec.consultation_baseline._source_bytes import (
    load_contract_bytes,
    parse_contract,
    sha256_bytes,
)

_POINTER_RE = re.compile(r"^/states/([^/]+)/transitions/(\d+)$")


@dataclass
class VerificationResult:
    """Итог независимой проверки."""

    source_sha256: str
    state_count: int
    transition_count: int
    source_sha256_match: bool
    state_id_set_match: bool
    transition_id_set_match: bool
    field_crosswalk_pass: bool
    gate_pass: bool
    errors: list[str]


def _load_jsonl_rows(jsonl_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"jsonl line {line_no}: invalid JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"jsonl line {line_no}: must be object")
            rows.append(obj)
    return rows


def _declaration_at_pointer(
    contract: dict[str, object],
    state_id: str,
    ordinal: int,
) -> tuple[str, str | None, str]:
    states = contract["states"]
    if state_id not in states:
        raise KeyError(f"state {state_id} missing in contract")
    state_obj = states[state_id]
    if not isinstance(state_obj, dict):
        raise TypeError(f"state {state_id} not an object")
    transitions = state_obj.get("transitions") or []
    if ordinal < 0 or ordinal >= len(transitions):
        raise IndexError(f"ordinal {ordinal} out of range for state {state_id}")
    tr = transitions[ordinal]
    if not isinstance(tr, dict):
        raise TypeError("transition not an object")
    trigger = tr.get("trigger")
    target = tr.get("target")
    if not isinstance(trigger, str) or not isinstance(target, str):
        raise TypeError("trigger/target must be strings")
    guard_val = tr.get("guard")
    guard: str | None
    if guard_val is None:
        guard = None
    elif isinstance(guard_val, str):
        guard = guard_val
    else:
        raise TypeError("guard must be string or null")
    return trigger, guard, target


def verify_independent(
    repo_root: Path,
    evidence_path: Path,
) -> VerificationResult:
    """Выполнить независимую проверку (1.7–1.9).

    Args:
        repo_root: корень репозитория.
        evidence_path: каталог с jsonl и manifest (manifest читается только для
            base_sha pin и сравнения заявленного source_sha256 / registry ids).

    Returns:
        VerificationResult с gate_pass.
    """

    errors: list[str] = []
    manifest_path = evidence_path / MANIFEST_FILENAME
    jsonl_path = evidence_path / JSONL_FILENAME
    if not manifest_path.is_file() or not jsonl_path.is_file():
        return VerificationResult(
            source_sha256="",
            state_count=0,
            transition_count=0,
            source_sha256_match=False,
            state_id_set_match=False,
            transition_id_set_match=False,
            field_crosswalk_pass=False,
            gate_pass=False,
            errors=["missing manifest or jsonl"],
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_sha = str(manifest["base_sha"])

    raw = load_contract_bytes(repo_root, base_sha)
    source_sha = sha256_bytes(raw)
    contract = parse_contract(raw)

    expected_states = state_ids_from_contract(contract)
    expected_transitions = expected_transition_ids(contract)

    source_sha_match = source_sha == manifest.get("source_sha256")
    if not source_sha_match:
        errors.append(
            f"source_sha256 mismatch: independent={source_sha} manifest={manifest.get('source_sha256')}"
        )

    manifest_states = sorted(manifest.get("state_ids") or [])
    state_match = manifest_states == expected_states
    if not state_match:
        errors.append(
            f"state_ids mismatch: expected {len(expected_states)} manifest {len(manifest_states)}"
        )

    rows = _load_jsonl_rows(jsonl_path)
    registry_ids = sorted(str(r.get("transition_id")) for r in rows)
    transition_match = registry_ids == expected_transitions
    if not transition_match:
        missing = set(expected_transitions) - set(registry_ids)
        extra = set(registry_ids) - set(expected_transitions)
        errors.append(
            f"transition_id set mismatch: missing={len(missing)} extra={len(extra)}"
        )

    crosswalk_ok = True
    if len(rows) != 392:
        crosswalk_ok = False
        errors.append(f"jsonl row count {len(rows)} != 392")

    for row in rows:
        tid = str(row.get("transition_id", ""))
        pointer = str(row.get("json_pointer", ""))
        source_state = str(row.get("source_state", ""))
        match = _POINTER_RE.match(pointer)
        if match is None:
            crosswalk_ok = False
            errors.append(f"{tid}: invalid json_pointer {pointer}")
            continue
        ptr_state, ptr_ord_s = match.group(1), match.group(2)
        ordinal = int(ptr_ord_s)
        expected_tid = transition_id_for(ptr_state, ordinal)
        if expected_tid != tid:
            crosswalk_ok = False
            errors.append(f"{tid}: transition_id != pointer-derived {expected_tid}")
        if ptr_state != source_state:
            crosswalk_ok = False
            errors.append(f"{tid}: source_state {source_state} != pointer state {ptr_state}")
        expected_pointer = json_pointer_for(ptr_state, ordinal)
        if pointer != expected_pointer:
            crosswalk_ok = False
            errors.append(f"{tid}: json_pointer format mismatch")
        try:
            exp_trigger, exp_guard, exp_target = _declaration_at_pointer(
                contract, ptr_state, ordinal
            )
        except (KeyError, IndexError, TypeError) as exc:
            crosswalk_ok = False
            errors.append(f"{tid}: cannot resolve source declaration: {exc}")
            continue
        if str(row.get("declared_trigger")) != exp_trigger:
            crosswalk_ok = False
            errors.append(f"{tid}: declared_trigger mismatch")
        row_guard = row.get("declared_guard")
        if row_guard is not None and not isinstance(row_guard, str):
            crosswalk_ok = False
            errors.append(f"{tid}: declared_guard must be string or null")
        elif row_guard != exp_guard:
            crosswalk_ok = False
            errors.append(f"{tid}: declared_guard mismatch")
        if str(row.get("declared_target")) != exp_target:
            crosswalk_ok = False
            errors.append(f"{tid}: declared_target mismatch")

    gate = (
        source_sha_match
        and state_match
        and transition_match
        and crosswalk_ok
        and len(expected_states) == 63
        and len(expected_transitions) == 392
    )
    return VerificationResult(
        source_sha256=source_sha,
        state_count=len(expected_states),
        transition_count=len(expected_transitions),
        source_sha256_match=source_sha_match,
        state_id_set_match=state_match,
        transition_id_set_match=transition_match,
        field_crosswalk_pass=crosswalk_ok,
        gate_pass=gate,
        errors=errors,
    )


def format_report(result: VerificationResult) -> str:
    """Текстовый отчёт для evidence."""

    lines = [
        "registry-independent-verification-report",
        f"source_sha256: {result.source_sha256}",
        f"state_count: {result.state_count}",
        f"transition_count: {result.transition_count}",
        f"SOURCE_SHA256_MATCH: {'PASS' if result.source_sha256_match else 'FAIL'}",
        f"STATE_ID_SET_MATCH: {'PASS' if result.state_id_set_match else 'FAIL'}",
        f"TRANSITION_ID_SET_MATCH: {'PASS' if result.transition_id_set_match else 'FAIL'}",
        f"FIELD_LEVEL_CROSSWALK: {'PASS' if result.field_crosswalk_pass else 'FAIL'}",
        f"GATE_1_10: {'PASS' if result.gate_pass else 'FAIL'}",
    ]
    if result.errors:
        lines.append("")
        lines.append("errors:")
        for err in result.errors[:50]:
            lines.append(f"  - {err}")
        if len(result.errors) > 50:
            lines.append(f"  ... and {len(result.errors) - 50} more")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, default=None)
    _ = parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    root = args.repo_root if args.repo_root is not None else repo_root_from_here()
    ev = args.evidence_dir if args.evidence_dir is not None else evidence_dir(root)
    result = verify_independent(root, ev)
    report_path = ev / "registry-independent-verification-report.txt"
    gate_path = ev / "registry-gate-1.10.json"
    report_path.write_text(format_report(result), encoding="utf-8")
    gate_path.write_text(
        json.dumps(
            {
                "gate": "1.10",
                "status": "PASS" if result.gate_pass else "FAIL",
                "source_sha256": result.source_sha256,
                "state_count": result.state_count,
                "transition_count": result.transition_count,
                "errors": result.errors,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(format_report(result))
    return 0 if result.gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
