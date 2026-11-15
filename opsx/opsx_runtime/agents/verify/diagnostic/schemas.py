"""Pydantic-модель ``diagnostic_result`` для Diagnostic Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.verify.repair_order.schemas import RepairOrderResult, SourceIdentity

AffectedArea = Literal["typing", "application_code", "tests", "configuration"]


class DiagnosticSourceIdentity(SourceIdentity):
    """Identity handoff из repair_order + версия upstream-схемы."""

    model_config = ConfigDict(extra="forbid")

    repair_order_schema_version: Literal["1.1"] = "1.1"


class MergedUnitLinkage(BaseModel):
    """Подтверждённая связь unit с anchor (не «тот же файл»)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    linkage_basis: str


class RepairUnit(BaseModel):
    """Минимальная логическая сущность repair: anchor + linked units."""

    model_config = ConfigDict(extra="forbid")

    unit_ids: list[str] = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    affected_areas: list[AffectedArea] = Field(default_factory=list)
    root_cause_summary: str
    change_boundaries: str
    merged_unit_linkages: list[MergedUnitLinkage] = Field(default_factory=list)


class RepairUnitAnswer(BaseModel):
    """JSON-ответ LLM: номера порядка units в repair unit (``repair_order.ordered_units.position``)."""

    model_config = ConfigDict(extra="forbid")

    ordered_positions: list[int] = Field(
        min_length=1,
        description=(
            "Номера position из repair_order для units этого repair unit; "
            "первый = anchor (минимальный position в очереди)"
        ),
    )
    affected_areas: list[AffectedArea] = Field(default_factory=list)
    root_cause_summary: str
    change_boundaries: str
    merged_unit_linkages: list[MergedUnitLinkage] = Field(default_factory=list)


class DiagnosticAnswer(BaseModel):
    """Минимальный JSON-ответ LLM diagnostic hop (без ``remaining_queue`` и без тел ошибок)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["diagnostic"] = "diagnostic"
    status: Literal["DIAGNOSED"] = "DIAGNOSED"
    anchor_unit_id: str
    repair_unit: RepairUnitAnswer


class RemainingQueueUnit(BaseModel):
    """Unit из ``ordered_units``, отложенный на следующий diagnostic hop."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    unit_type: Literal["GROUP", "UNGROUPED"]
    unit_id: str


class DiagnosticResult(BaseModel):
    """Один repair unit + остаток очереди repair_order (без regroup triage)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["diagnostic"] = "diagnostic"
    status: Literal["DIAGNOSED", "PAUSE"]
    source_identity: DiagnosticSourceIdentity
    anchor_unit_id: str
    repair_unit: RepairUnit | None = None
    remaining_queue: list[RemainingQueueUnit] = Field(default_factory=list)
    pause_reason: str | None = None


def source_identity_from_repair_order(
    repair_order: RepairOrderResult,
) -> DiagnosticSourceIdentity:
    """Построить ``source_identity`` из ``RepairOrderResult.source_identity``."""
    upstream = repair_order.source_identity
    return DiagnosticSourceIdentity(
        change_id=upstream.change_id,
        execution_id=upstream.execution_id,
        revision=upstream.revision,
        identity_status=upstream.identity_status,
        group_error_schema_version=upstream.group_error_schema_version,
        repair_order_schema_version=repair_order.schema_version,
    )
