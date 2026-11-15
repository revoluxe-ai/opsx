"""Pydantic-модель ``repair_order_result`` для Repair Order Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SortCriterion = Literal[
    "EXPLICIT_DEPENDENCY",
    "FINDING_DEFINITENESS",
    "POTENTIAL_EFFECT",
    "LIMITED_SCOPE",
    "STABLE_TIE_BREAK",
]

from agents.verify.group_error.schemas import AnalysisIdentity, RepairWorkflowKind


class SourceIdentity(BaseModel):
    """Identity handoff из group_error triage + версия upstream-схемы."""

    model_config = ConfigDict(extra="forbid")

    change_id: str | None = None
    execution_id: str | None = None
    revision: str | None = None
    identity_status: Literal["VERIFIED", "PARTIAL", "MISMATCH"] = "PARTIAL"
    group_error_schema_version: Literal["1.4", "1.5"] = "1.5"


class FindingWorkflowRef(BaseModel):
    """Provenance одного finding для выбора repair workflow."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    source_tool: Literal["pytest", "pyright", "mypy"] | None = None
    repair_workflow: RepairWorkflowKind
    category: str | None = None


class OrderedUnit(BaseModel):
    """Одна processing unit после group_error (schema 1.5 — всегда GROUP, ``unit_id=error_id``)."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    unit_type: Literal["GROUP", "UNGROUPED"]
    unit_id: str
    finding_ids: list[str] = Field(default_factory=list)
    finding_workflows: list[FindingWorkflowRef] = Field(default_factory=list)
    primary_sort_criterion: SortCriterion | None = None
    placement_rationale: str


class ConfirmedDependency(BaseModel):
    """Явная блокирующая зависимость, подтверждённая только входом triage."""

    model_config = ConfigDict(extra="forbid")

    predecessor_unit_id: str
    successor_unit_id: str
    basis: str


class RepairOrderAnswer(BaseModel):
    """Минимальный JSON-ответ LLM: только порядок ``unit_id`` (без тел ошибок)."""

    model_config = ConfigDict(extra="forbid")

    ordered_unit_ids: list[str] = Field(
        default_factory=list,
        description=(
            "unit_id в порядке repair priority: каждый id — ``GroupedError.error_id``"
        ),
    )


class RepairOrderResult(BaseModel):
    """Упорядоченный список repair units после group_error (без regroup/diagnose)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    agent: Literal["repair_order"] = "repair_order"
    status: Literal["SORTED", "PAUSE"]
    source_identity: SourceIdentity
    ordered_units: list[OrderedUnit] = Field(default_factory=list)
    confirmed_dependencies: list[ConfirmedDependency] = Field(default_factory=list)
    pause_reason: str | None = None


def source_identity_from_analysis(
    analysis_identity: AnalysisIdentity,
    *,
    group_error_schema_version: Literal["1.4", "1.5"] = "1.5",
) -> SourceIdentity:
    """Построить ``source_identity`` из ``GroupErrorResult.analysis_identity``."""
    return SourceIdentity(
        change_id=analysis_identity.change_id,
        execution_id=analysis_identity.execution_id,
        revision=analysis_identity.revision,
        identity_status=analysis_identity.identity_status,
        group_error_schema_version=group_error_schema_version,
    )
