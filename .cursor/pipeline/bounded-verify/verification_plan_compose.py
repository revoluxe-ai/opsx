# -*- coding: utf-8 -*-
"""Составление DRAFT bounded verification plan из registry waves (без дублирования pack HOW).

Единственный supported entry для finite ``required_checks`` перед ``freeze_plan``.
Не пишет ledger и не выполняет checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from bounded_verify_plan import (
    DEFAULT_REGISTRY_PATH,
    PlanDraft,
    BoundedVerifyPlanError,
    draft_plan,
)
from verification_obligations import (
    registry_check_ids_for_profile,
    unsupported_mandatory_obligations,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

PROFILE_WAVES: dict[str, tuple[str, ...]] = {
    "static_fast": ("A", "U", "I"),
    "static_full": ("A", "U", "I", "D"),
    "bounded_minimal": ("A",),
    # full_required uses registry FULL waves; adapter obligations validated separately.
    "full_required": ("A", "U", "I", "D"),
}


@dataclass(frozen=True)
class ComposeResult:
    """Результат compose: DRAFT plan metadata."""

    change_id: str
    profile: str
    check_ids: tuple[str, ...]
    draft: PlanDraft


def _load_registry(registry_path: Path) -> Mapping[str, Any]:
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoundedVerifyPlanError("REGISTRY_CHECK_UNKNOWN", "registry invalid")
    return data


def checks_for_profile(
    profile: str,
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    mode: str = "FULL",
) -> list[dict[str, Any]]:
    """Возвращает ordered ``required_checks`` rows для ``draft_plan``.

    Args:
        profile: ``static_fast`` | ``static_full`` | ``bounded_minimal``.
        registry_path: Read-only registry.yaml.
        mode: Registry modes key (FAST vs FULL) — wave D только при FULL.

    Returns:
        Список ``{"check_id", "order"}`` без дублирования check_id.

    Raises:
        BoundedVerifyPlanError: неизвестный profile или registry.
    """
    wave_ids = PROFILE_WAVES.get(profile)
    if wave_ids is None:
        raise BoundedVerifyPlanError(
            "PLAN_DRAFT_INVALID",
            f"unknown profile: {profile}",
        )
    registry = _load_registry(registry_path)
    modes = registry.get("modes")
    if not isinstance(modes, dict):
        raise BoundedVerifyPlanError("REGISTRY_CHECK_UNKNOWN", "registry modes missing")
    mode_block = modes.get(mode if profile == "static_full" else "FAST")
    if not isinstance(mode_block, dict):
        raise BoundedVerifyPlanError("REGISTRY_CHECK_UNKNOWN", f"mode missing: {mode}")
    waves = mode_block.get("waves")
    if not isinstance(waves, list):
        raise BoundedVerifyPlanError("REGISTRY_CHECK_UNKNOWN", "waves missing")

    ordered_ids: list[str] = []
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        if str(wave.get("id", "")) not in wave_ids:
            continue
        checks = wave.get("checks")
        if not isinstance(checks, list):
            continue
        for check_id in checks:
            cid = str(check_id).strip()
            if cid and cid not in ordered_ids:
                ordered_ids.append(cid)

    if not ordered_ids:
        raise BoundedVerifyPlanError(
            "PLAN_DRAFT_INVALID",
            f"profile {profile} yielded no checks",
        )

    return [
        {"check_id": check_id, "order": index + 1}
        for index, check_id in enumerate(ordered_ids)
    ]


def compose_verification_plan(
    change_id: str,
    profile: str = "static_fast",
    *,
    root: Path = REPO_ROOT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    mode: str = "FULL",
    allow_incomplete_coverage: bool = False,
) -> ComposeResult:
    """DRAFT plan через ``draft_plan`` с checks из registry waves.

    Args:
        change_id: OpenSpec change id (requires tasks.md impl-complete signal).
        profile: Wave subset profile.
        root: Repo root.
        registry_path: Registry path.
        mode: FULL for destructive wave when profile is static_full.
        allow_incomplete_coverage: If false, ``full_required`` fails when mandatory
            adapter obligations lack registry check_id (FAIL_CLOSED).

    Returns:
        ComposeResult with PlanDraft from ``draft_plan``.
    """
    if profile == "full_required" and allow_incomplete_coverage:
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            "allow_incomplete_coverage is forbidden for profile=full_required",
        )

    unsupported = unsupported_mandatory_obligations(profile)
    if unsupported:
        missing = ", ".join(o.obligation_id for o in unsupported)
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            f"mandatory obligations without machine-ready adapter: {missing}",
        )

    if profile == "full_required":
        from bounded_verify_s5_adapters import assert_no_unsupported_mandatory_adapters, frontier_adapter_check_ids
        from bounded_verify_s5_obligation_applicability import (
            assert_full_required_conditional_coverage,
            applicable_conditional_check_ids,
        )

        assert_no_unsupported_mandatory_adapters(root=root)
        assert_full_required_conditional_coverage(change_id, root=root)
        check_ids = list(registry_check_ids_for_profile("static_full")) + list(
            frontier_adapter_check_ids(root=root)
        ) + list(applicable_conditional_check_ids(change_id, root=root))
        required = [
            {"check_id": cid, "order": index + 1}
            for index, cid in enumerate(check_ids)
        ]
    else:
        required = checks_for_profile(
            profile,
            registry_path=registry_path,
            mode=mode if profile in ("static_full", "full_required") else "FAST",
        )
    plan_draft = draft_plan(
        change_id,
        required,
        root=root,
        registry_path=registry_path,
        verification_profile=profile,
    )
    return ComposeResult(
        change_id=change_id,
        profile=profile,
        check_ids=tuple(row["check_id"] for row in required),
        draft=plan_draft,
    )
