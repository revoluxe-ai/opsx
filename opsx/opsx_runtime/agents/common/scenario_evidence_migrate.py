"""Детерминированная миграция durable scenario evidence после canonical layout / v3 digest.

Переписывает PASS receipts, observations и ``row_walk_journal`` на актуальные
``row_digest`` и ``test_node_id`` без повторного audit/repair, если review
уже был SUPPORTED (или durable GAP) до рефакторинга.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import write_pass
from agents.common.scenario_files import (
    plan_has_embedded_rows,
    read_scenario_ids,
    scenario_test_relpath,
)
from agents.common.scenario_migrate import check_coverage, iter_coverage_dirs
from agents.verify.scenario_coverage_verify.delta_rows import (
    ROW_CONTRACT,
    impact_scope,
    row_freshness_token,
)
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    scenario_verifier_contract_digest,
    source_paths,
)
from agents.verify.scenario_coverage_verify.input_pack import load_scenario_coverage_plan_bundle
from agents.verify.scenario_coverage_verify.review_semantics import (
    review_proof_error,
    spec_clauses,
    trace_mappings,
)
from agents.verify.scenario_coverage_verify.row_receipts import (
    ROW_PASS_CONTRACT,
    ROW_PASS_VERDICT,
    read_observation,
    row_check,
    row_key,
    save_supported_row,
    write_observation,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageRowReview,
)

_ROW_RECEIPT_CONTRACT = "scenario_row_evidence_v1"
_DEFAULT_MODEL: Mapping[str, Any] = {"id": "composer-2.5", "options": []}
_SCENARIO_ID_SPEC = re.compile(
    r"SCENARIO_ID:\s*(openspec/changes/[^\s]+::[^\n]+)",
)


@dataclass
class MigrationReport:
    """Итог миграции evidence для одного change."""

    change_id: str
    migrated_supported: int = 0
    migrated_gap: int = 0
    skipped_no_prior: int = 0
    repair_proof_remap: int = 0
    repair_layout: int = 0
    repair: list[str] = field(default_factory=list)
    matrix_rebound: int = 0
    ledger_refreshed: bool = False
    layout_problems: list[str] = field(default_factory=list)


def rebind_derived_matrix_entries(coverage: Path, *, dry_run: bool = True) -> tuple[int, list[str]]:
    """Синхронизировать ``DERIVED::`` matrix с plan ``scenario_id`` (no-op если совпадает).

    Если в canonical test есть единственный spec-bound ``openspec/...`` в docstring —
    подменяем matrix entry (редкий случай нормализации id).
    """
    from agents.common.scenario_files import trace_matrix_machine_path

    matrix_path = trace_matrix_machine_path(coverage)
    if not matrix_path.is_file():
        return 0, []
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return 0, []
    repo = coverage.parents[3]
    changes: list[str] = []
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("scenario_id") or "")
        node = str(entry.get("test_node_id") or "")
        if not sid.startswith("DERIVED::") or "::" not in node:
            continue
        rel = node.split("::", 1)[0]
        test_path = resource_path(repo, rel)
        if not test_path.is_file():
            continue
        text = test_path.read_text(encoding="utf-8")
        spec_ids = [m.group(1).strip() for m in _SCENARIO_ID_SPEC.finditer(text)]
        if len(spec_ids) != 1 or spec_ids[0] == sid:
            continue
        entry["scenario_id"] = spec_ids[0]
        count += 1
        changes.append(f"{sid} -> {spec_ids[0]}")
    if count and not dry_run:
        matrix_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return count, changes


def _read_receipt_relaxed(root: Path, change_id: str, scenario_id: str) -> dict[str, Any] | None:
    """Прочитать supported receipt без проверки ``input_digest``."""
    from workflows.artifact_paths import legacy_verify_dir, receipts_dir

    path = receipts_dir(root, change_id) / "check-receipts" / f"{row_check(scenario_id)}.json"
    if not path.is_file():
        path = (
            legacy_verify_dir(root, change_id)
            / "check-receipts"
            / f"{row_check(scenario_id)}.json"
        )
    if path.is_symlink() or not path.is_file():
        return None
    try:
        item = json.loads(path.read_bytes())
        if not isinstance(item, dict) or item.get("status") != "PASS":
            return None
        return item
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _journal_row_review(
    repo: Path, change_id: str, scenario_id: str,
) -> ScenarioCoverageRowReview | None:
    from workflows.artifact_paths import scenario_machine_receipt_path

    journal_path = scenario_machine_receipt_path(repo, change_id, "row_walk_journal.json")
    if not journal_path.is_file():
        return None
    try:
        data = json.loads(journal_path.read_text(encoding="utf-8"))
        rec = data.get(scenario_id) if isinstance(data, dict) else None
        if not isinstance(rec, dict):
            return None
        recheck = rec.get("recheck")
        if not isinstance(recheck, dict):
            return None
        reviews = recheck.get("row_reviews")
        if not isinstance(reviews, list) or not reviews:
            return None
        row = ScenarioCoverageRowReview.model_validate(reviews[0])
        if row.assessment != "SUPPORTED":
            return None
        return row
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _provenance_from_receipt(receipt: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    origin = str(receipt.get("origin_execution_id") or "")
    evidence = str(receipt.get("evidence_id") or "")
    extra = {
        "evidence_migrated_from": {
            "input_digest": receipt.get("input_digest"),
            "test_node_id": (receipt.get("result") or {}).get("row", {}).get("test_node_id"),
        },
    }
    return origin, evidence, extra


def _function_range(path: Path, node_id: str) -> tuple[list[str], int, int]:
    from agents.verify.scenario_coverage_verify.review_semantics import _test_function_lines

    return _test_function_lines(path, node_id)


def _line_map_across_functions(
    old_lines: list[str],
    old_first: int,
    old_last: int,
    new_lines: list[str],
    new_first: int,
    new_last: int,
) -> dict[int, int]:
    """Сопоставить абсолютные номера строк test-функции (difflib по strip)."""
    old_slice = [
        (i + 1, old_lines[i].strip())
        for i in range(old_first - 1, min(old_last, len(old_lines)))
    ]
    new_slice = [
        (i + 1, new_lines[i].strip())
        for i in range(new_first - 1, min(new_last, len(new_lines)))
    ]
    if not old_slice or not new_slice:
        return {}
    matcher = difflib.SequenceMatcher(
        a=[text for _, text in old_slice],
        b=[text for _, text in new_slice],
        autojunk=False,
    )
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            span = min(i2 - i1, j2 - j1)
            for k in range(span):
                old_no = old_slice[i1 + k][0]
                new_no = new_slice[j1 + k][0]
                mapping[old_no] = new_no
        elif tag == "replace":
            span = min(i2 - i1, j2 - j1)
            for k in range(span):
                mapping[old_slice[i1 + k][0]] = new_slice[j1 + k][0]
    return mapping


def _remap_proofs(
    repo: Path,
    *,
    old_node: str,
    new_node: str,
    when_proofs: list[int],
    outcome_proofs: list[int],
) -> tuple[list[int], list[int]] | None:
    """Перенумеровать proof lines со старого mapped test на canonical."""
    old_rel, new_rel = old_node.split("::", 1)[0], new_node.split("::", 1)[0]
    new_path = resource_path(repo, new_rel)
    if not new_path.is_file():
        return None
    new_lines, new_first, new_last = _function_range(new_path, new_node)
    old_path = resource_path(repo, old_rel)
    old_lines: list[str] = []
    old_first, old_last = 1, len(new_lines)
    if old_rel == new_rel:
        old_lines, old_first, old_last = new_lines, new_first, new_last
    elif old_path.is_file():
        try:
            old_lines, old_first, old_last = _function_range(old_path, old_node)
        except ValueError:
            old_lines = old_path.read_text(encoding="utf-8").splitlines()
            old_first, old_last = 1, len(old_lines)
    else:
        return None

    line_map = _line_map_across_functions(
        old_lines, old_first, old_last, new_lines, new_first, new_last,
    )

    def map_one(n: int) -> int | None:
        if old_rel == new_rel:
            return n if new_first <= n <= new_last else None
        if n in line_map:
            return line_map[n]
        if not 1 <= n <= len(old_lines):
            return None
        needle = old_lines[n - 1].strip()
        if not needle:
            return None
        hits = [
            i + 1
            for i in range(new_first - 1, min(new_last, len(new_lines)))
            if new_lines[i].strip() == needle
        ]
        if len(hits) == 1:
            return hits[0]
        if len(needle) >= 20:
            partial = [
                i + 1
                for i in range(new_first - 1, min(new_last, len(new_lines)))
                if needle in new_lines[i] or new_lines[i].strip() in needle
            ]
            if len(partial) == 1:
                return partial[0]
        return None

    new_when: list[int] = []
    for n in when_proofs:
        mapped = map_one(n)
        if mapped is None:
            return None
        new_when.append(mapped)
    new_out: list[int] = []
    for n in outcome_proofs:
        mapped = map_one(n)
        if mapped is None:
            return None
        new_out.append(mapped)
    return new_when, new_out


def _rebuild_proofs_from_canonical(
    repo: Path,
    change_id: str,
    scenario_id: str,
    new_node: str,
    *,
    when_count: int,
    outcome_count: int,
) -> tuple[list[int], list[int]] | None:
    """Взять WHEN=await/outcome=assert строки из canonical test (после сильного rewrite)."""
    rel = new_node.split("::", 1)[0]
    path = resource_path(repo, rel)
    if not path.is_file():
        return None
    lines, first, last = _function_range(path, new_node)
    if when_count == 0 and outcome_count == 0:
        return [], []
    doc_markers = ("Смысл:", "SCENARIO_ID:", "Исходные условия:", '"""', "'''")

    def _executable(i: int) -> bool:
        raw = lines[i].strip()
        if not raw or raw.startswith("#"):
            return False
        if any(m in lines[i] for m in doc_markers):
            return False
        return True

    assert_lines = [
        i + 1
        for i in range(first - 1, min(last, len(lines)))
        if (
            lines[i].strip().startswith(("assert ", "assert(", "await assert_"))
            or "pytest.raises(" in lines[i]
        )
    ]
    first_assert = assert_lines[0] if assert_lines else last + 1
    when_lines = [
        i + 1
        for i in range(first - 1, min(first_assert - 1, len(lines)))
        if _executable(i)
        and (
            "await " in lines[i]
            or not lines[i].strip().startswith(("assert ", "assert(", "def ", "@"))
        )
    ]
    if when_count and len(when_lines) < when_count:
        return None
    if outcome_count and len(assert_lines) < outcome_count:
        return None
    return when_lines[-when_count:], assert_lines[:outcome_count]


def _load_prior_row(
    repo: Path, change_id: str, scenario_id: str,
) -> tuple[ScenarioCoverageRowReview | None, dict[str, Any]]:
    journal_row = _journal_row_review(repo, change_id, scenario_id)
    if journal_row is not None:
        return journal_row, {}
    receipt = _read_receipt_relaxed(repo, change_id, scenario_id)
    if receipt is None:
        return None, {}
    result = receipt.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("row"), dict):
        return None, {}
    try:
        row = ScenarioCoverageRowReview.model_validate(result["row"])
    except (ValueError, TypeError):
        return None, {}
    _, _, extra = _provenance_from_receipt(receipt)
    return row, extra


def _authority(repo: Path, change_id: str) -> dict[str, Any]:
    bundle = load_scenario_coverage_plan_bundle(change_id, repo_root=repo)
    plan_id = str((bundle.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or "")
    verifier = scenario_verifier_contract_digest(repo, _DEFAULT_MODEL)
    return {
        "model": dict(_DEFAULT_MODEL),
        "plan_evidence_id": plan_id,
        "verifier_contract_digest": verifier,
    }


def _refresh_aggregate_ledger(
    repo: Path,
    change_id: str,
    *,
    plan_ids: list[str],
    mapping: Mapping[str, str],
    authority: Mapping[str, Any],
    provenance: Mapping[str, Mapping[str, str]],
    rows: Mapping[str, ScenarioCoverageRowReview],
    dry_run: bool,
) -> bool:
    from agents.verify.scenario_coverage_verify.delta_rows import ledger_from_validated_rows

    scope = impact_scope(
        repo, source_paths(repo, change_id), mapping, authority=authority,
    )
    from workflows.artifact_paths import legacy_verify_dir, receipts_dir

    ledger_path = receipts_dir(repo, change_id) / "check-receipts" / "scenario_coverage_rows.json"
    if not ledger_path.is_file():
        ledger_path = (
            legacy_verify_dir(repo, change_id) / "check-receipts" / "scenario_coverage_rows.json"
        )
    if not ledger_path.is_file():
        return False
    supported_ids = [
        sid for sid in plan_ids
        if sid in rows and rows[sid].assessment == "SUPPORTED"
    ]
    if supported_ids != plan_ids:
        return False
    serialized = [rows[sid].model_dump(mode="json") for sid in plan_ids]
    try:
        ledger = ledger_from_validated_rows(
            plan_ids=plan_ids,
            current_scope=scope,
            row_reviews=serialized,
            provenance=provenance,
        )
    except ValueError:
        return False
    if dry_run:
        return True
    write_pass(
        root=repo,
        change_id=change_id,
        check="scenario_coverage_rows",
        contract=_ROW_RECEIPT_CONTRACT,
        input_digest=scope["shared_digest"],
        origin_execution_id=provenance.get(plan_ids[0], {}).get("origin_execution_id", "evidence-migration"),
        evidence_id=provenance.get(plan_ids[0], {}).get("evidence_id", "evidence-migration"),
        pass_verdict="ROW_SET_VALID",
        result=ledger,
        verifier_contract_digest=None,
    )
    return True


def migrate_change_evidence(
    repo: Path,
    change_id: str,
    *,
    dry_run: bool = True,
) -> MigrationReport:
    """Мигрировать evidence одного change. ``dry_run`` — только отчёт."""
    repo = repo.resolve()
    report = MigrationReport(change_id=change_id)
    coverage = resource_path(repo, "openspec", "changes", change_id, "scenario-coverage")
    if not coverage.is_dir():
        report.layout_problems.append("COVERAGE_MISSING")
        report.repair_layout += 1
        return report
    plan = coverage / "plan.md"
    if plan.is_file() and plan_has_embedded_rows(plan.read_text(encoding="utf-8")):
        report.layout_problems.append("COVERAGE_PLAN_EMBEDDED")
        report.repair_layout += 1
        return report
    rebound, _changes = rebind_derived_matrix_entries(coverage, dry_run=dry_run)
    report.matrix_rebound = rebound
    layout = check_coverage(coverage, repo=repo, require_canonical_tests=True)
    if layout:
        report.layout_problems.extend(layout)
        report.repair_layout += 1
        return report
    try:
        plan_ids = read_scenario_ids(coverage, allow_historical=False)
        mapping = trace_mappings(repo, change_id)
    except ValueError as exc:
        report.layout_problems.append(str(exc))
        report.repair_layout += 1
        return report
    authority = _authority(repo, change_id)
    scope = impact_scope(
        repo, source_paths(repo, change_id), mapping, authority=authority,
    )
    snapshot = input_snapshot(repo, change_id)
    source_digest = str(snapshot["source_digest"])
    plan_evidence_id = str(authority["plan_evidence_id"])
    from agents.common.scenario_files import row_walk_journal_machine_path

    journal_path = row_walk_journal_machine_path(coverage)
    journal: dict[str, Any] = {}
    if journal_path.is_file():
        try:
            loaded = json.loads(journal_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                journal = loaded
        except (OSError, json.JSONDecodeError):
            journal = {}
    provenance: dict[str, dict[str, str]] = {}
    supported_rows: dict[str, ScenarioCoverageRowReview] = {}

    for scenario_id in plan_ids:
        new_node = mapping.get(scenario_id, "")
        if not new_node:
            report.repair.append(scenario_id)
            report.repair_proof_remap += 1
            continue
        row_digest = scope["row_digests"].get(scenario_id)
        if not row_digest:
            report.repair.append(scenario_id)
            report.repair_proof_remap += 1
            continue
        prior, _extra = _load_prior_row(repo, change_id, scenario_id)
        if prior is None:
            obs = read_observation(repo, change_id=change_id, scenario_id=scenario_id)
            if obs and obs.get("assessment") == "GAP" and isinstance(obs.get("row"), dict):
                try:
                    gap_row = ScenarioCoverageRowReview.model_validate(obs["row"])
                except (ValueError, TypeError):
                    report.skipped_no_prior += 1
                    continue
                remapped = _remap_proofs(
                    repo,
                    old_node=gap_row.test_node_id or new_node,
                    new_node=new_node,
                    when_proofs=list(gap_row.when_proofs),
                    outcome_proofs=list(gap_row.outcome_proofs),
                )
                if remapped is None:
                    report.repair.append(scenario_id)
                    report.repair_proof_remap += 1
                    continue
                gap_row = gap_row.model_copy(
                    update={
                        "test_node_id": new_node,
                        "when_proofs": remapped[0],
                        "outcome_proofs": remapped[1],
                    },
                )
                origin = str(obs.get("origin_execution_id") or "evidence-migration")
                evidence = str(obs.get("evidence_id") or origin)
                if not dry_run:
                    gaps_raw = obs.get("gaps") or []
                    gaps = [ScenarioCoverageGap.model_validate(g) for g in gaps_raw]
                    write_observation(
                        repo,
                        change_id=change_id,
                        row=gap_row,
                        row_digest=row_digest,
                        source_digest=source_digest,
                        plan_evidence_id=plan_evidence_id,
                        origin_execution_id=origin,
                        evidence_id=evidence,
                        shared_digest=scope["shared_digest"],
                        row_identity_contract=ROW_CONTRACT,
                        gaps=gaps,
                    )
                report.migrated_gap += 1
                continue
            report.skipped_no_prior += 1
            continue
        old_node = prior.test_node_id or new_node
        remapped = _remap_proofs(
            repo,
            old_node=old_node,
            new_node=new_node,
            when_proofs=list(prior.when_proofs),
            outcome_proofs=list(prior.outcome_proofs),
        )
        def _clause_counts() -> tuple[int, int]:
            try:
                when_clauses, outcome_clauses = spec_clauses(repo, change_id, scenario_id)
            except ValueError:
                return len(prior.when_proofs), len(prior.outcome_proofs)
            return len(when_clauses) or len(prior.when_proofs), len(outcome_clauses) or len(
                prior.outcome_proofs,
            )

        if remapped is None:
            wc, oc = _clause_counts()
            remapped = _rebuild_proofs_from_canonical(
                repo, change_id, scenario_id, new_node, when_count=wc, outcome_count=oc,
            )
        row = prior.model_copy(
            update={
                "scenario_id": scenario_id,
                "test_node_id": new_node,
                "when_proofs": remapped[0] if remapped else prior.when_proofs,
                "outcome_proofs": remapped[1] if remapped else prior.outcome_proofs,
            },
        ) if remapped else None
        if row is None or review_proof_error(repo, change_id, row, expected_node=new_node) is not None:
            wc, oc = _clause_counts()
            rebuilt = _rebuild_proofs_from_canonical(
                repo, change_id, scenario_id, new_node, when_count=wc, outcome_count=oc,
            )
            if rebuilt is None:
                report.repair.append(scenario_id)
                report.repair_proof_remap += 1
                continue
            row = prior.model_copy(
                update={
                    "scenario_id": scenario_id,
                    "test_node_id": new_node,
                    "when_proofs": rebuilt[0],
                    "outcome_proofs": rebuilt[1],
                },
            )
            if review_proof_error(repo, change_id, row, expected_node=new_node) is not None:
                report.repair.append(scenario_id)
                report.repair_proof_remap += 1
                continue
        receipt = _read_receipt_relaxed(repo, change_id, scenario_id)
        if receipt:
            origin, evidence, _ = _provenance_from_receipt(receipt)
        else:
            origin, evidence = "evidence-migration", row_key(scenario_id)[:32]
        if not origin.strip():
            origin = "evidence-migration"
        if not evidence.strip():
            evidence = row_key(scenario_id)[:32]
        provenance[scenario_id] = {
            "origin_execution_id": origin,
            "evidence_id": evidence,
        }
        supported_rows[scenario_id] = row
        if not dry_run:
            _ = save_supported_row(
                repo,
                change_id=change_id,
                row=row,
                mapping=mapping,
                row_digest=row_digest,
                plan_evidence_id=plan_evidence_id,
                origin_execution_id=origin,
                evidence_id=evidence,
                row_identity_contract=ROW_CONTRACT,
            )
            jrec = journal.get(scenario_id)
            if not isinstance(jrec, dict):
                jrec = {}
            token = row_freshness_token(repo, change_id, scenario_id, new_node)
            recheck = jrec.get("recheck") if isinstance(jrec.get("recheck"), dict) else {}
            recheck = {**recheck, "row_reviews": [row.model_dump(mode="json")]}
            journal[scenario_id] = {
                **jrec,
                "token": token,
                "recheck": recheck,
            }
        report.migrated_supported += 1

    if not dry_run and journal_path.parent.is_dir():
        journal_path.write_text(
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if supported_rows and len(supported_rows) == len(plan_ids):
        report.ledger_refreshed = _refresh_aggregate_ledger(
            repo,
            change_id,
            plan_ids=plan_ids,
            mapping=mapping,
            authority=authority,
            provenance=provenance,
            rows=supported_rows,
            dry_run=dry_run,
        )
    return report


def migrate_all_changes(repo: Path, *, dry_run: bool = True) -> list[MigrationReport]:
    """Прогон по всем ``scenario-coverage`` каталогам."""
    reports: list[MigrationReport] = []
    for coverage in iter_coverage_dirs(repo):
        change_id = coverage.parent.name
        reports.append(migrate_change_evidence(repo, change_id, dry_run=dry_run))
    return reports
