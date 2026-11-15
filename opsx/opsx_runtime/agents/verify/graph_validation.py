"""Валидация patch'ей verify-subgraph (маршруты и apply hop)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from agents.implementation.apply.schemas import ApplyAnswer

ImplVerifyRouteTarget = Literal[
    "scenario_coverage_verify",
    "coverage_activation",
    "apply",
    "verify_owner_attention",
]

ScenarioCoverageRouteTarget = Literal[
    "impl_verify",
    "coverage_activation",
    "verify_apply_commit",
    "verify_owner_attention",
]

CoverageActivationRouteTarget = Literal[
    "static_repair",
    "verify_skip_forward",
]


class VerifyImplVerifyRouteDecision(BaseModel):
    """Допустимый исход conditional edge после ``impl_verify``."""

    model_config = ConfigDict(extra="forbid")

    verdict: str
    verify_green_restart_count: int = 0
    verify_green_max_restarts: int | None = None
    target: ImplVerifyRouteTarget

    @model_validator(mode="after")
    def route_matches_verdict(self) -> "VerifyImplVerifyRouteDecision":
        verdict = str(self.verdict or "")
        if verdict == "IMPL_OK" and self.target not in (
            "scenario_coverage_verify",
            "coverage_activation",
        ):
            raise ValueError("IMPL_OK must route to scenario_coverage_verify or coverage_activation")
        if verdict != "IMPL_GAPS" and self.target in ("apply", "verify_owner_attention"):
            raise ValueError("apply branch requires IMPL_GAPS")
        return self


class ApplyGraphPatch(BaseModel):
    """Patch ``GraphState`` после ``ApplyAgent`` (контракт для commit hop)."""

    model_config = ConfigDict(extra="ignore")

    apply_status: str
    apply_marker: str
    apply_report_text: str
    apply_answer: dict[str, Any]
    verify_finding_fix: dict[str, Any] | None = None

    @model_validator(mode="after")
    def answer_matches_status(self) -> "ApplyGraphPatch":
        parsed = ApplyAnswer.model_validate(self.apply_answer)
        if parsed.status != self.apply_status:
            raise ValueError("apply_answer.status must match apply_status")
        if parsed.status in ("APPLY_COMPLETE", "APPLY_PARTIAL") and parsed.files_changed:
            if self.verify_finding_fix is None:
                raise ValueError("file-changing apply requires verify_finding_fix")
        return self


def resolve_impl_verify_route_target(state: dict[str, Any]) -> ImplVerifyRouteTarget:
    """Target после ``impl_verify``: apply-loop, scenario subgraph или skip в ``coverage_activation``."""
    from workflows.verify.green_loop import resolve_verify_fail_apply_target

    verdict = str(state.get("verdict") or "")
    if verdict == "IMPL_GAPS":
        return resolve_verify_fail_apply_target(state)  # type: ignore[return-value]
    # Never treat a checkpointed green bit as fresh scenario evidence.
    # The scenario node checks the durable receipt cheaply and calls the
    # model only on a real input/contract change.
    return "scenario_coverage_verify"


def _scenario_commit_paths(state: dict[str, Any]) -> bool:
    paths = state.get("scenario_row_commit_paths")
    return isinstance(paths, list) and any(str(path).strip() for path in paths)


def resolve_scenario_coverage_route_target(state: dict[str, Any]) -> ScenarioCoverageRouteTarget:
    """После построчного обхода: commit на строку уже в walk; дальше по verdict.

    ``apply_status`` здесь не участвует. Незакрытый FAIL без файлов остаётся
    на owner attention. Timeout и неизвестный исход уже пришли как UNKNOWN.
    ``scenario_row_commit_paths`` — legacy batch; row walk его больше не выставляет.
    """
    from workflows.verify.cycle_checks import CheckName, cycle_checks

    verdict = str(state.get("scenario_coverage_verdict") or "")
    if verdict == "SCENARIO_TEST_COVERAGE_PASS":
        if _scenario_commit_paths(state):
            return "verify_apply_commit"
        if not cycle_checks(state).is_established(CheckName.IMPL_VERIFY):
            return "impl_verify"
        return "coverage_activation"
    if verdict == "SCENARIO_TEST_COVERAGE_FAIL" and _scenario_commit_paths(state):
        return "verify_apply_commit"
    return "verify_owner_attention"


def resolve_coverage_activation_route_target(state: dict[str, Any]) -> CoverageActivationRouteTarget:
    """Всегда вести verify в ``static_repair`` после hop coverage activation.

    Verdict в state (PASS/BLOCKED/NEEDS_OWNER) остаётся в receipt, но не останавливает subgraph.
    """
    _ = state
    return "static_repair"


def validate_coverage_activation_route(state: dict[str, Any], target: str) -> None:
    expected = resolve_coverage_activation_route_target(state)
    if target != expected:
        raise ValueError(
            f"coverage_activation route mismatch: got {target!r}, expected {expected!r}",
        )


def validate_scenario_coverage_route(state: dict[str, Any], target: str) -> None:
    """Проверить маршрут после ``scenario_coverage_verify``."""
    expected = resolve_scenario_coverage_route_target(state)
    if target != expected:
        raise ValueError(
            f"scenario_coverage_verify route mismatch: got {target!r}, expected {expected!r}",
        )


def validate_impl_verify_route(state: dict[str, Any], target: str) -> None:
    """Проверить, что маршрут после ``impl_verify`` согласован с контрактом."""
    expected = resolve_impl_verify_route_target(state)
    decision = VerifyImplVerifyRouteDecision(
        verdict=str(state.get("verdict") or ""),
        verify_green_restart_count=int(state.get("verify_green_restart_count") or 0),
        verify_green_max_restarts=state.get("verify_green_max_restarts"),
        target=expected,
    )
    if target != decision.target:
        raise ValueError(
            f"impl_verify route mismatch: got {target!r}, expected {decision.target!r}",
        )


def validate_apply_patch(patch: dict[str, Any]) -> ApplyGraphPatch:
    """Провалидировать patch apply-агента перед записью в state."""
    return ApplyGraphPatch.model_validate(patch)
