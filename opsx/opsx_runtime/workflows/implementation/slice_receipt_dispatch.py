"""JSON-контракт детерминированной проверки рецепта для hop (без участия модели)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workflows.implementation.slice_check_workflow_receipt import (
    SliceWorkflowReceiptEvaluation,
    evaluate_slice_stage,
    persist_after_successful_hop,
)
from workflows.implementation.slice_receipt import SliceReceiptValidation


class SliceReceiptValidationAnswer(BaseModel):
    """Готовый ответ проверки рецепта, передаётся агенту в ``answer`` (обязательный JSON)."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    code: str | None = None
    work_paths: list[str] = Field(default_factory=list)
    stage: str = Field(min_length=1)
    slice_id: str = Field(min_length=1)
    gate: Literal["PRE_AGENT", "POST_AGENT", "SKIP", "SKIP_MIGRATE"]

    @field_validator("work_paths")
    @classmethod
    def _paths_posix(cls, paths: list[str]) -> list[str]:
        return sorted({_posix_item(p) for p in paths if p})

    @classmethod
    def from_validation(
        cls,
        validation: SliceReceiptValidation | SliceWorkflowReceiptEvaluation,
        *,
        stage: str,
        slice_id: str,
        gate: Literal["PRE_AGENT", "POST_AGENT", "SKIP", "SKIP_MIGRATE"],
    ) -> SliceReceiptValidationAnswer:
        """Собрать валидируемый JSON из результата gate."""
        backfilled = (
            validation.backfilled_digest
            if isinstance(validation, SliceWorkflowReceiptEvaluation)
            else False
        )
        paths = list(validation.work_paths)
        return cls(
            valid=validation.valid,
            code=validation.code,
            work_paths=paths,
            stage=stage,
            slice_id=slice_id,
            gate="SKIP_MIGRATE" if gate == "SKIP" and backfilled else gate,
        )


def _posix_item(path: str) -> str:
    return str(path).replace("\\", "/")


def parse_slice_receipt_validation_answer(raw: object) -> SliceReceiptValidationAnswer:
    """Строго разобрать dispatch JSON (тесты и prompt wiring)."""
    if isinstance(raw, SliceReceiptValidationAnswer):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("slice_receipt_validation must be a JSON object")
    return SliceReceiptValidationAnswer.model_validate(raw)


def refresh_receipt_fingerprints(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> SliceReceiptValidation:
    """POST-hop: persist ``work_scope_digest`` и перепроверить gate."""
    evaluated = persist_after_successful_hop(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
    )
    return SliceReceiptValidation(evaluated.valid, evaluated.code, evaluated.work_paths)


def evaluate_for_hop(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
    state: Mapping[str, object] | None = None,
) -> SliceWorkflowReceiptEvaluation:
    """PRE-hop gate (в т.ч. миграция digest без агента)."""
    graph_state: Mapping[str, Any] | None = (
        state if isinstance(state, dict) else None
    )
    return evaluate_slice_stage(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
        state=graph_state,
    )
