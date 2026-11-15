"""Контракт JSON-ответа audit ``/opsx-spec-scenario-coverage mode=audit`` в runtime."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioCoverageGap(BaseModel):
    """Один gap между durable plan и фактическим test evidence."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, description="Идентификатор строки plan / Spec Scenario.")
    gap_class: str = Field(
        min_length=1,
        description="Класс gap (например MISSING_OR_WEAK_SCENARIO_TEST, STUB_CONTRACT_MISMATCH).",
    )
    evidence: str = Field(
        min_length=1,
        description="Краткое read-only доказательство: путь теста, цитата plan row, отсутствие seam.",
    )


class ScenarioCoverageRowReview(BaseModel):
    """Read-only assessment of exactly one frozen scenario row."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    test_node_id: str = Field(default="", description="Mapped test node or empty if missing.")
    assessment: Literal["SUPPORTED", "GAP", "CONTRACT_CONFLICT", "INSPECTION_BLOCKED"]
    evidence: str = Field(min_length=1, description="Concrete mapped-test/assurance evidence.")
    when_proofs: list[int] = Field(
        default_factory=list, description="Absolute 1-based line number in mapped test SOURCE FILE (not function-relative) per WHEN clause.",
    )
    outcome_proofs: list[int] = Field(
        default_factory=list, description="Absolute 1-based line number in mapped test SOURCE FILE (not function-relative) per THEN/AND clause.",
    )


ScenarioCoverageVerdict = Literal[
    "SCENARIO_TEST_COVERAGE_PASS",
    "SCENARIO_TEST_COVERAGE_FAIL",
    "SCENARIO_TEST_COVERAGE_UNKNOWN",
]


class ScenarioCoverageVerifyModelAnswer(BaseModel):
    """Ответ LLM-аудита: только смысловое суждение. Привязку к плану/снимку делает runtime."""

    model_config = ConfigDict(extra="forbid")

    verdict: ScenarioCoverageVerdict
    row_reviews: list[ScenarioCoverageRowReview] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    gaps: list[ScenarioCoverageGap] = Field(default_factory=list)
    block_reason: str = Field(
        default="",
        description="Обязателен при SCENARIO_TEST_COVERAGE_UNKNOWN; иначе пустая строка.",
    )
    local_hop_copy_block: str = Field(
        min_length=1,
        description="Owner-local блок: workspace, change, verdict, gaps (без выдуманных slash-команд).",
    )

    @model_validator(mode="after")
    def check_consistency(self) -> "ScenarioCoverageVerifyModelAnswer":
        ids = [row.scenario_id for row in self.row_reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("row_reviews must contain unique scenario_id values")
        if self.row_reviews:
            for row in self.row_reviews:
                if row.assessment == "SUPPORTED" and (
                    not row.when_proofs or not row.outcome_proofs
                ):
                    raise ValueError(
                        "SUPPORTED requires source-locatable WHEN/THEN proofs "
                        f"for {row.scenario_id}"
                    )
            gap_ids = {gap.scenario_id for gap in self.gaps}
            row_gap_ids = {row.scenario_id for row in self.row_reviews if row.assessment == "GAP"}
            if self.verdict == "SCENARIO_TEST_COVERAGE_FAIL" and row_gap_ids != gap_ids:
                raise ValueError("gaps must match every GAP row review exactly")
            if self.verdict == "SCENARIO_TEST_COVERAGE_PASS" and any(
                row.assessment != "SUPPORTED" for row in self.row_reviews
            ):
                raise ValueError("PASS requires every row review SUPPORTED")
            if self.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" and any(
                row.assessment in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED")
                for row in self.row_reviews
            ):
                raise ValueError("unresolved contract/inspect rows require UNKNOWN")
        if self.verdict == "SCENARIO_TEST_COVERAGE_FAIL" and not self.gaps:
            raise ValueError("FAIL requires at least one gap")
        if self.verdict == "SCENARIO_TEST_COVERAGE_PASS" and self.gaps:
            raise ValueError("PASS must not include gaps")
        if self.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN" and not self.block_reason.strip():
            raise ValueError("UNKNOWN requires block_reason")
        if self.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" and self.block_reason.strip():
            raise ValueError("block_reason allowed only for UNKNOWN")
        return self


class ScenarioCoverageVerifyAnswer(BaseModel):
    """Durable verdict with machine-bound plan/source identity (receipts, graph state)."""

    model_config = ConfigDict(extra="forbid")

    verdict: ScenarioCoverageVerdict
    plan_evidence_id: str = Field(default="", description="Identity from frozen plan receipt.")
    source_digest: str = Field(
        default="", description="Content digest of plan/specs/tests/production source at audit start.",
    )
    row_reviews: list[ScenarioCoverageRowReview] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    gaps: list[ScenarioCoverageGap] = Field(default_factory=list)
    block_reason: str = Field(
        default="",
        description="Обязателен при SCENARIO_TEST_COVERAGE_UNKNOWN; иначе пустая строка.",
    )
    local_hop_copy_block: str = Field(
        min_length=1,
        description="Owner-local блок: workspace, change, verdict, gaps (без выдуманных slash-команд).",
    )

    @model_validator(mode="after")
    def check_consistency(self) -> "ScenarioCoverageVerifyAnswer":
        ids = [row.scenario_id for row in self.row_reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("row_reviews must contain unique scenario_id values")
        if self.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" and not self.source_digest:
            raise ValueError("PASS/FAIL requires a machine-bound source_digest")
        if self.row_reviews:
            for row in self.row_reviews:
                if row.assessment == "SUPPORTED" and (
                    not row.when_proofs or not row.outcome_proofs
                ):
                    raise ValueError(
                        "SUPPORTED requires source-locatable WHEN/THEN proofs "
                        f"for {row.scenario_id}"
                    )
            gap_ids = {gap.scenario_id for gap in self.gaps}
            row_gap_ids = {row.scenario_id for row in self.row_reviews if row.assessment == "GAP"}
            if self.verdict == "SCENARIO_TEST_COVERAGE_FAIL" and row_gap_ids != gap_ids:
                raise ValueError("gaps must match every GAP row review exactly")
            if self.verdict == "SCENARIO_TEST_COVERAGE_PASS" and any(
                row.assessment != "SUPPORTED" for row in self.row_reviews
            ):
                raise ValueError("PASS requires every row review SUPPORTED")
            if self.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" and any(
                row.assessment in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED")
                for row in self.row_reviews
            ):
                raise ValueError("unresolved contract/inspect rows require UNKNOWN")
        if self.verdict == "SCENARIO_TEST_COVERAGE_FAIL" and not self.gaps:
            raise ValueError("FAIL requires at least one gap")
        if self.verdict == "SCENARIO_TEST_COVERAGE_PASS" and self.gaps:
            raise ValueError("PASS must not include gaps")
        if self.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN" and not self.block_reason.strip():
            raise ValueError("UNKNOWN requires block_reason")
        if self.verdict != "SCENARIO_TEST_COVERAGE_UNKNOWN" and self.block_reason.strip():
            raise ValueError("block_reason allowed only for UNKNOWN")
        return self


def model_answer_from_durable(
    answer: ScenarioCoverageVerifyAnswer | Mapping[str, Any],
) -> ScenarioCoverageVerifyModelAnswer:
    """Drop machine-bound identity fields for LLM replay stubs and recordings."""
    payload = (
        answer.model_dump(mode="json")
        if isinstance(answer, ScenarioCoverageVerifyAnswer)
        else dict(answer)
    )
    payload.pop("plan_evidence_id", None)
    payload.pop("source_digest", None)
    return ScenarioCoverageVerifyModelAnswer.model_validate(payload)


def bind_scenario_coverage_authority(
    model_answer: ScenarioCoverageVerifyModelAnswer | Mapping[str, Any],
    *,
    plan_evidence_id: str,
    source_digest: str,
) -> ScenarioCoverageVerifyAnswer:
    """Attach frozen plan/source identity after LLM judgment; anti-replay is machine-side."""
    payload = (
        model_answer.model_dump(mode="json")
        if isinstance(model_answer, ScenarioCoverageVerifyModelAnswer)
        else dict(model_answer)
    )
    return ScenarioCoverageVerifyAnswer(
        plan_evidence_id=plan_evidence_id,
        source_digest=source_digest,
        **payload,
    )
