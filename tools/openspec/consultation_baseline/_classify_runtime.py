"""Классификация registry rows по design D-BL-02 (hybrid JSON + Python)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.openspec.consultation_baseline._cta_transitions import simple_cta_transition_keys
from tools.openspec.consultation_baseline._fsm_preempt_index import (
    build_preempt_rules,
    first_matching_preempt,
)
from tools.openspec.consultation_baseline._handler_semantics import handler_semantic_for_trigger
from tools.openspec.consultation_baseline._registry_build import RegistryRow
from tools.openspec.consultation_baseline._runtime_dynamics import RuntimeDynamics, load_runtime_dynamics


@dataclass(frozen=True)
class ClassifiedFields:
    """Поля runtime-классификации для одной строки реестра."""

    runtime_path_primary: str
    runtime_paths: list[str]
    python_preempt_ref: str | None
    handler_adapter_ref: str | None
    evidence_status: str
    gap: str


def classify_transition(
    source_state: str,
    declared_trigger: str,
    cta_keys: set[tuple[str, str]],
    preempt_rules: list[object],
    *,
    declared_guard: str | None = None,
) -> ClassifiedFields:
    """Классифицировать одну declared transition (D-BL-02).

    Порядок: HANDLER_ADAPTER (semantic) → PYTHON_FSM_PREEMPT → JSON_SIMPLE_CTA → UNMAPPED.
    """

    runtime_paths: list[str] = []
    handler_ref: str | None = None
    preempt_ref: str | None = None
    conditional_notes: list[str] = []

    semantic = handler_semantic_for_trigger(declared_trigger)
    if semantic is not None:
        handler_ref = semantic.ref
        runtime_paths.append(f"HANDLER_ADAPTER:{semantic.ref}")
        if semantic.conditional:
            conditional_notes.append(semantic.note)

    preempt = first_matching_preempt(source_state, declared_trigger, preempt_rules)
    if preempt is not None:
        preempt_ref = preempt.ref
        runtime_paths.append(f"PYTHON_FSM_PREEMPT:{preempt.ref}")

    in_cta = (source_state, declared_trigger) in cta_keys
    if in_cta:
        runtime_paths.append("JSON_SIMPLE_CTA:ux_contract.CTA_TRANSITIONS")

    if semantic is not None:
        primary = "HANDLER_ADAPTER"
        gap = (
            f"handler ingress @ BASE_SHA ({semantic.ref}); "
            "classification≠execution proof; evidence_status UNVERIFIED"
        )
        if conditional_notes:
            gap += "; conditional: " + "; ".join(conditional_notes)
        if declared_guard:
            gap += f"; declared_guard={declared_guard!r}"
        return ClassifiedFields(
            runtime_path_primary=primary,
            runtime_paths=_dedupe_paths(runtime_paths),
            python_preempt_ref=preempt_ref,
            handler_adapter_ref=handler_ref,
            evidence_status="UNVERIFIED",
            gap=gap,
        )

    if preempt is not None:
        primary = "PYTHON_FSM_PREEMPT"
        gap = (
            f"FSM preempt before CTA_TRANSITIONS ({preempt_ref}); "
            "static trace @ BASE_SHA; not TEST_EXECUTED_PASS"
        )
        if in_cta:
            gap += "; JSON catalog also declares simple target (preempt wins)"
        if declared_guard:
            gap += f"; guard may diverge from JSON: {declared_guard!r}"
        return ClassifiedFields(
            runtime_path_primary=primary,
            runtime_paths=_dedupe_paths(runtime_paths),
            python_preempt_ref=preempt_ref,
            handler_adapter_ref=None,
            evidence_status="UNVERIFIED",
            gap=gap,
        )

    if in_cta:
        return ClassifiedFields(
            runtime_path_primary="JSON_SIMPLE_CTA",
            runtime_paths=_dedupe_paths(runtime_paths),
            python_preempt_ref=None,
            handler_adapter_ref=None,
            evidence_status="UNVERIFIED",
            gap=(
                "Resolved via ux_contract.CTA_TRANSITIONS after FSM preempt scan; "
                "IMPL@BASE_SHA static only"
            ),
        )

    return ClassifiedFields(
        runtime_path_primary="UNMAPPED_AT_BASELINE",
        runtime_paths=_dedupe_paths(runtime_paths) if runtime_paths else [],
        python_preempt_ref=None,
        handler_adapter_ref=None,
        evidence_status="UNVERIFIED",
        gap=(
            "No characterized handler semantic, FSM preempt, or CTA_TRANSITIONS key "
            f"for ({source_state!r}, {declared_trigger!r}) @ BASE_SHA"
        ),
    )


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def apply_classification_to_row(
    row: RegistryRow,
    fields: ClassifiedFields,
) -> RegistryRow:
    """Вернуть новый RegistryRow с полями классификации."""

    return RegistryRow(
        transition_id=row.transition_id,
        base_sha=row.base_sha,
        source_state=row.source_state,
        declared_trigger=row.declared_trigger,
        declared_guard=row.declared_guard,
        declared_target=row.declared_target,
        json_pointer=row.json_pointer,
        runtime_path_primary=fields.runtime_path_primary,
        evidence_status=fields.evidence_status,
        gap=fields.gap,
        runtime_paths=tuple(fields.runtime_paths),
        python_preempt_ref=fields.python_preempt_ref,
        handler_adapter_ref=fields.handler_adapter_ref,
    )


def classify_registry_rows(
    contract: dict[str, Any],
    base_sha: str,
    repo_root: Path,
    dynamics: RuntimeDynamics | None = None,
) -> list[RegistryRow]:
    """Классифицировать все structural rows контракта."""

    from tools.openspec.consultation_baseline._registry_build import iter_registry_rows  # noqa: PLC0415

    dyn = dynamics if dynamics is not None else load_runtime_dynamics(repo_root, base_sha)
    cta_keys = simple_cta_transition_keys(contract)
    preempt_rules = build_preempt_rules(repo_root, base_sha, dyn)
    classified: list[RegistryRow] = []
    for row in iter_registry_rows(contract, base_sha):
        fields = classify_transition(
            row.source_state,
            row.declared_trigger,
            cta_keys,
            preempt_rules,
            declared_guard=row.declared_guard,
        )
        classified.append(apply_classification_to_row(row, fields))
    return classified
