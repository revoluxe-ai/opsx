# -*- coding: utf-8 -*-
"""Versioned verification profile + policy identity for bounded-verify plans (P0-A/B)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bounded_verify_plan import BoundedVerifyPlanError
from bounded_verify_snapshot import PLAN_SCHEMA_V2
from verification_obligations import registry_check_ids_for_profile

REPO_ROOT = Path(__file__).resolve().parents[3]

OBLIGATION_SET_ID = "bounded-verify-obligation-set-v1"

VERIFICATION_PROFILES: frozenset[str] = frozenset(
    {"static_fast", "static_full", "bounded_minimal", "full_required"}
)
DIAGNOSTIC_PROFILES: frozenset[str] = frozenset(
    {"static_fast", "static_full", "bounded_minimal"}
)

FULL_REQUIRED_VERIFICATION_COMPLETE = "FULL_REQUIRED_VERIFICATION_COMPLETE"
BOUNDED_STATIC_VERIFICATION_COMPLETE = "BOUNDED_STATIC_VERIFICATION_COMPLETE"
LEGACY_BOUNDED_VERIFICATION_COMPLETE = "LEGACY_BOUNDED_VERIFICATION_COMPLETE"

_POLICY_PATHS: tuple[str, ...] = (
    ".cursor/static-check/registry.yaml",
    "openspec/policies/VERIFY_POLICY.md",
    "opsx/project/capabilities.yaml",
    ".cursor/pipeline/bounded-verify/verification_obligations.py",
)


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_revision_bindings(*, root: Path) -> dict[str, str]:
    """Machine-checkable policy source revisions bound at freeze."""
    bindings: dict[str, str] = {}
    for rel in _POLICY_PATHS:
        p = root / rel
        if p.is_file():
            bindings[rel] = _digest_file(p)
    return bindings


def assert_policy_revisions_complete(*, root: Path) -> None:
    """Fail-closed when any required policy source is missing at freeze."""
    bindings = policy_revision_bindings(root=root)
    missing = [rel for rel in _POLICY_PATHS if rel not in bindings]
    if missing:
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            f"required policy sources missing: {missing}",
        )


def _applicability_decisions(change_id: str, *, root: Path) -> list[dict[str, str]]:
    from bounded_verify_s5_obligation_applicability import (
        resolve_performance_review_applicability,
        resolve_scenario_test_coverage_applicability,
    )

    rows: list[dict[str, str]] = []
    for resolver in (
        resolve_scenario_test_coverage_applicability,
        resolve_performance_review_applicability,
    ):
        row = resolver(change_id, root=root)
        rows.append(
            {
                "obligation_id": row.obligation_id,
                "disposition": row.disposition.value,
                "notes": row.notes,
            }
        )
    return rows


def exact_required_check_ids_for_profile(
    change_id: str,
    profile: str,
    *,
    root: Path = REPO_ROOT,
    registry_path: Path | None = None,
) -> tuple[str, ...]:
    """Exact mandatory ``required_checks`` set for profile (freeze must match)."""
    if profile not in VERIFICATION_PROFILES:
        raise BoundedVerifyPlanError("PLAN_DRAFT_INVALID", f"unknown verification_profile: {profile}")

    if profile in DIAGNOSTIC_PROFILES:
        from bounded_verify_plan import DEFAULT_REGISTRY_PATH
        from verification_plan_compose import checks_for_profile

        reg = registry_path or DEFAULT_REGISTRY_PATH
        rows = checks_for_profile(profile, registry_path=reg)
        return tuple(str(r["check_id"]) for r in rows)

    if profile == "full_required":
        from verification_obligations import frontier_adapter_obligations

        from bounded_verify_s5_obligation_applicability import applicable_conditional_check_ids

        static_ids = registry_check_ids_for_profile("static_full")
        adapter_ids = tuple(
            o.obligation_id for o in frontier_adapter_obligations("full_required")
        )
        conditional_ids = applicable_conditional_check_ids(change_id, root=root)
        merged: list[str] = []
        for cid in (*static_ids, *adapter_ids, *conditional_ids):
            if cid not in merged:
                merged.append(cid)
        return tuple(merged)

    return registry_check_ids_for_profile(profile)


def build_verification_policy(
    change_id: str,
    profile: str,
    *,
    root: Path = REPO_ROOT,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Policy identity block embedded in v2 plans."""
    assert_policy_revisions_complete(root=root)
    mandatory = exact_required_check_ids_for_profile(
        change_id, profile, root=root, registry_path=registry_path
    )
    from bounded_verify_s5_obligation_applicability import (
        applicability_decisions_snapshot,
        collect_conditional_inputs_digest,
    )

    return {
        "obligation_set_id": OBLIGATION_SET_ID,
        "verification_profile": profile,
        "policy_revisions": policy_revision_bindings(root=root),
        "mandatory_required_check_ids": list(mandatory),
        "applicability_decisions": applicability_decisions_snapshot(change_id, root=root),
        "conditional_inputs_digest": collect_conditional_inputs_digest(change_id, root=root),
    }


def assert_freeze_mandatory_coverage(
    change_id: str,
    profile: str,
    check_ids: Sequence[str],
    *,
    root: Path = REPO_ROOT,
    registry_path: Path | None = None,
) -> None:
    """Enforce exact obligation coverage at freeze (not compose-only)."""
    if profile not in VERIFICATION_PROFILES:
        raise BoundedVerifyPlanError("PLAN_DRAFT_INVALID", f"unknown verification_profile: {profile}")

    expected = exact_required_check_ids_for_profile(
        change_id, profile, root=root, registry_path=registry_path
    )
    actual = tuple(sorted(str(c) for c in check_ids))
    expected_sorted = tuple(sorted(expected))
    if actual != expected_sorted:
        missing = sorted(set(expected_sorted) - set(actual))
        extra = sorted(set(actual) - set(expected_sorted))
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            f"freeze coverage mismatch profile={profile} missing={missing} extra={extra}",
        )

    from bounded_verify_s5_adapters import adapter_implemented

    if profile == "full_required":
        from bounded_verify_s5_obligation_applicability import (
            assert_full_required_conditional_coverage,
            assert_plan_required_checks_match_conditional_policy,
        )

        assert_full_required_conditional_coverage(change_id, root=root)
        assert_plan_required_checks_match_conditional_policy(
            change_id, tuple(check_ids), root=root
        )
        for oid in check_ids:
            if oid in expected and oid not in registry_check_ids_for_profile("static_full"):
                if not adapter_implemented(oid, root=root):
                    raise BoundedVerifyPlanError(
                        "PLAN_COVERAGE_INCOMPLETE",
                        f"freeze re-check: adapter not READY for {oid}",
                    )
    else:
        for oid in check_ids:
            if not adapter_implemented(oid, root=root) and oid not in registry_check_ids_for_profile(
                profile
            ):
                raise BoundedVerifyPlanError(
                    "PLAN_COVERAGE_INCOMPLETE",
                    f"diagnostic profile {profile} cannot include non-registry obligation {oid}",
                )


def plan_profile_from_document(doc: Mapping[str, Any]) -> str | None:
    """Profile from v2 plan; None for legacy v1."""
    raw = doc.get("verification_profile")
    if raw is None:
        return None
    return str(raw)


def assert_full_completion_attestation_allowed(plan: Mapping[str, Any]) -> None:
    """Fail-closed before emitting FULL_REQUIRED_VERIFICATION_COMPLETE."""
    schema = str(plan.get("schema_version", ""))
    profile = plan_profile_from_document(plan)
    if profile != "full_required" or schema != PLAN_SCHEMA_V2:
        raise BoundedVerifyPlanError(
            "PLAN_INVALID",
            "full completion attestation requires v2 full_required plan",
        )
    policy = plan.get("verification_policy")
    if not isinstance(policy, dict):
        raise BoundedVerifyPlanError("PLAN_INVALID", "verification_policy missing")
    if str(policy.get("verification_profile", "")) != "full_required":
        raise BoundedVerifyPlanError("PLAN_INVALID", "policy profile mismatch")
    mandatory = policy.get("mandatory_required_check_ids")
    checks = plan.get("required_checks")
    if not isinstance(mandatory, list) or not isinstance(checks, list):
        raise BoundedVerifyPlanError("PLAN_INVALID", "mandatory/checks invalid")
    plan_ids = sorted(str(row["check_id"]) for row in checks if isinstance(row, dict))
    if sorted(str(x) for x in mandatory) != plan_ids:
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            "required_checks do not match policy mandatory set",
        )
    revisions = policy.get("policy_revisions")
    if not isinstance(revisions, dict) or len(revisions) < len(_POLICY_PATHS):
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            "policy revisions incomplete for full attestation",
        )


def ledger_completion_attestation(plan: Mapping[str, Any]) -> str:
    """Semantic completion certificate; static/legacy never implies full_required."""
    schema = str(plan.get("schema_version", ""))
    profile = plan_profile_from_document(plan)
    if profile == "full_required" and schema == PLAN_SCHEMA_V2:
        try:
            assert_full_completion_attestation_allowed(plan)
        except BoundedVerifyPlanError:
            return BOUNDED_STATIC_VERIFICATION_COMPLETE
        return FULL_REQUIRED_VERIFICATION_COMPLETE
    if profile in DIAGNOSTIC_PROFILES:
        return BOUNDED_STATIC_VERIFICATION_COMPLETE
    return LEGACY_BOUNDED_VERIFICATION_COMPLETE


def policy_identity_for_fingerprint(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Stable subset of verification_policy for plan fingerprint."""
    return {
        "obligation_set_id": policy.get("obligation_set_id"),
        "verification_profile": policy.get("verification_profile"),
        "policy_revisions": policy.get("policy_revisions"),
        "mandatory_required_check_ids": policy.get("mandatory_required_check_ids"),
        "applicability_decisions": policy.get("applicability_decisions"),
        "conditional_inputs_digest": policy.get("conditional_inputs_digest"),
    }
