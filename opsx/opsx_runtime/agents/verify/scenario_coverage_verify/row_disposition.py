"""Детерминированное evidence-решение по одной строке scenario coverage.

Отвечает только на вопрос: на текущем ``row_digest`` строка **SUPPORTED и valid**
(или для batch — durable negative reuse) или нужен check→repair.

Не входит: repair lease, ``repair_attempts``, PAUSE verify — это оркестратор
``row_cycle``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT, impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    scenario_verifier_contract_digest,
    source_paths,
)
from agents.verify.scenario_coverage_verify.input_pack import load_scenario_coverage_plan_bundle
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_reusable_terminal_row,
    read_supported_row,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageRowReview,
)

DispositionMode = Literal["walk", "batch", "status"]


@dataclass(frozen=True)
class ScenarioCoverageRowContext:
    """Неподвижный снимок mapping и digests для resolve; без LLM hop/journal."""

    repo_root: Path
    change_id: str
    plan_evidence_id: str
    mapping: Mapping[str, str]
    row_digests: Mapping[str, str | None]
    source_digest: str
    shared_digest: str


@dataclass(frozen=True)
class ScenarioRowWork:
    """Только evidence. PAUSE/repair attempts — не здесь."""

    run_check_repair_cycle: bool
    reason: str
    row_digest: str | None
    row_review: ScenarioCoverageRowReview | None
    provenance: Mapping[str, str] | None = None
    gaps: tuple[ScenarioCoverageGap, ...] | None = None
    block_reason: str | None = None


def build_scenario_coverage_row_context(
    repo_root: Path,
    change_id: str,
    *,
    authority: Mapping[str, Any],
) -> ScenarioCoverageRowContext:
    """Собрать ctx для ``resolve_row_work`` (mapping + v3 row digests)."""
    root = repo_root.resolve()
    model = authority.get("model")
    if not isinstance(model, Mapping) or not model.get("id"):
        raise ValueError("ROW_CONTEXT_MODEL_REQUIRED")
    plan_evidence_id = str(authority.get("plan_evidence_id") or "").strip()
    if not plan_evidence_id:
        bundle = load_scenario_coverage_plan_bundle(change_id, repo_root=root)
        receipt = bundle.get("plan_receipt")
        if isinstance(receipt, Mapping):
            plan_evidence_id = str(receipt.get("PLAN_EVIDENCE_ID") or "").strip()
    verifier_contract_digest = str(
        authority.get("verifier_contract_digest")
        or scenario_verifier_contract_digest(root, model)
    ).strip()
    mapping = trace_mappings(root, change_id)
    scope = impact_scope(
        root,
        source_paths(root, change_id),
        mapping,
        authority={
            "model": dict(model),
            "plan_evidence_id": plan_evidence_id,
            "verifier_contract_digest": verifier_contract_digest,
        },
    )
    snapshot = input_snapshot(root, change_id)
    return ScenarioCoverageRowContext(
        repo_root=root,
        change_id=change_id,
        plan_evidence_id=plan_evidence_id,
        mapping=mapping,
        row_digests=scope["row_digests"],
        source_digest=str(snapshot["source_digest"]),
        shared_digest=str(scope["shared_digest"]),
    )


def resolve_row_work(
    ctx: ScenarioCoverageRowContext,
    scenario_id: str,
    *,
    disposition_mode: DispositionMode = "batch",
) -> ScenarioRowWork:
    """Нужен ли полный check→(repair)→recheck (True) или строка settled (False).

    Walk: False только при валидном SUPPORTED receipt на ``row_digest``.
    Batch: False также при durable negative observation на том же digest.
    Status: как walk для bool (CLI использует отдельное отображение).
    """
    if scenario_id not in ctx.mapping:
        return ScenarioRowWork(
            run_check_repair_cycle=True,
            reason="UNKNOWN_SCENARIO",
            row_digest=None,
            row_review=None,
        )
    row_digest = ctx.row_digests.get(scenario_id)
    if not row_digest:
        return ScenarioRowWork(
            run_check_repair_cycle=True,
            reason="MAPPED_TEST_MISSING",
            row_digest=None,
            row_review=None,
        )
    supported = read_supported_row(
        ctx.repo_root,
        change_id=ctx.change_id,
        scenario_id=scenario_id,
        mapping=ctx.mapping,
        row_digest=row_digest,
        plan_evidence_id=ctx.plan_evidence_id,
        row_identity_contract=ROW_CONTRACT,
    )
    if supported is not None:
        row, origin = supported
        return ScenarioRowWork(
            run_check_repair_cycle=False,
            reason="FRESH_SUPPORTED_RECEIPT",
            row_digest=row_digest,
            row_review=row,
            provenance=origin,
        )
    if disposition_mode == "batch":
        negative = read_reusable_terminal_row(
            ctx.repo_root,
            change_id=ctx.change_id,
            scenario_id=scenario_id,
            expected_node=ctx.mapping[scenario_id],
            row_digest=row_digest,
            shared_digest=ctx.shared_digest,
            source_digest=ctx.source_digest,
            plan_evidence_id=ctx.plan_evidence_id,
            row_identity_contract=ROW_CONTRACT,
        )
        if negative is not None:
            row, gaps, block, origin = negative
            return ScenarioRowWork(
                run_check_repair_cycle=False,
                reason="DURABLE_GAP_REUSE_BATCH",
                row_digest=row_digest,
                row_review=row,
                provenance=origin,
                gaps=tuple(gaps),
                block_reason=block or None,
            )
    return ScenarioRowWork(
        run_check_repair_cycle=True,
        reason="NO_SUPPORTED_RECEIPT",
        row_digest=row_digest,
        row_review=None,
    )
