"""Pydantic-модель ``analysis_result.json`` для Group Error Agent."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

GateToolId = Literal["pytest", "pyright", "mypy"]

RepairWorkflowKind = Literal[
    "WF_PYTEST_COLLECTION",
    "WF_PYTEST_FAILURE",
    "WF_PYRIGHT_TYPE",
    "WF_PYRIGHT_LINT",
    "WF_MYPY_TYPE",
    "WF_MYPY_IMPORT",
    "WF_MYPY_CONTRACT",
    "WF_UNKNOWN_GATE",
]


class AnalysisIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str | None = None
    execution_id: str | None = None
    revision: str | None = None
    identity_status: Literal["VERIFIED", "PARTIAL", "MISMATCH"] = "PARTIAL"


class InputIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["COMPLETE", "PARTIAL", "INVALID"] = "PARTIAL"
    issues: list[str] = Field(default_factory=list)


class GroupedError(BaseModel):
    """Одна логическая ошибка и инструменты gates, которые её зафиксировали."""

    model_config = ConfigDict(extra="forbid")

    error_id: str
    problem: str
    match_basis: str = ""
    instruments: list[GateToolId] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    linked_evidence: list[str] = Field(default_factory=list)
    match_evidence: list[str] = Field(default_factory=list)


class FindingGroup(BaseModel):
    """Legacy 1.4: вход LLM до normalize (alias полей — ``GroupedError``)."""

    model_config = ConfigDict(extra="forbid")

    group_id: str
    finding_ids: list[str] = Field(default_factory=list)
    matched_problem: str
    match_basis: str
    diagnostic_sources: list[GateToolId] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    linked_evidence: list[str] = Field(default_factory=list)
    match_evidence: list[str] = Field(default_factory=list)


class AdditionalFindingLink(BaseModel):
    """Дополнительная связь finding с группой без дублирования основного членства."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    related_group_id: str
    note: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    bug_id: str | None = None
    source_tool: GateToolId | None = None
    repair_workflow: RepairWorkflowKind | None = None
    category: str | None = None
    title: str | None = None
    observation: str | None = None
    expected: str | None = None
    classification: Literal["FACT", "INFERENCE"] = "FACT"
    criterion_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    causal_group_id: str | None = None
    scope: str | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_repair_workflow(cls, data: object) -> object:
        """Подставить ``repair_workflow``, если не задан явно."""
        if not isinstance(data, dict):
            return data
        if data.get("repair_workflow") is not None:
            return data
        from agents.verify.group_error.repair_workflow import classify_repair_workflow

        tool = data.get("source_tool")
        observation = str(data.get("observation") or "")
        return {
            **data,
            "repair_workflow": classify_repair_workflow(
                source_tool=tool if tool in ("pytest", "pyright", "mypy") else None,
                observation=observation,
            ),
        }


class TriageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings_total: int = 0
    finding_groups_total: int = 0
    ungrouped_total: int = 0


class GroupErrorResult(BaseModel):
    """Корневой объект triage handoff (persist как ``analysis_result.json``)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.4", "1.5"] = "1.4"
    agent: Literal["group_error"] = "group_error"
    analysis_identity: AnalysisIdentity
    input_integrity: InputIntegrity
    grouped_errors: list[GroupedError] = Field(default_factory=list)
    finding_groups: list[FindingGroup] = Field(default_factory=list)
    ungrouped_finding_ids: list[str] = Field(default_factory=list)
    additional_finding_links: list[AdditionalFindingLink] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    triage_summary: TriageSummary

    @model_validator(mode="after")
    def _triage_partition_invariants(self) -> Self:
        """Partition findings ↔ grouped_errors (нормализация 1.4 → 1.5 внутри проверки)."""
        from agents.verify.group_error.validation import validate_group_error_triage_invariants

        validate_group_error_triage_invariants(self)
        return self
