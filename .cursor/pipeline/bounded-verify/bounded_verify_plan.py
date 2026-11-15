# -*- coding: utf-8 -*-
"""Единственный supported mutation authority для bounded verification plan (R-V1-005A).

Публикует `openspec/changes/<change_id>/bounded-verify/plan.json` через
`bounded_verify_atomic`; команда `/opsx-plan-verification` — interface only.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional, Sequence

import yaml

from bounded_verify_atomic import (
    PLAN_SCHEMA_VERSION,
    atomic_publish_json,
    canonical_json_bytes,
    read_canonical_json,
)
PLAN_SCHEMA_V2 = "bounded-verify-plan-v2"

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_SCHEMA_PATH = (
    REPO_ROOT
    / ".cursor"
    / "pipeline"
    / "contracts"
    / "bounded-verify"
    / "PLAN_SCHEMA.json"
)
DEFAULT_REGISTRY_PATH = REPO_ROOT / ".cursor" / "static-check" / "registry.yaml"

PLAN_FROZEN_MUTATION_REFUSED = "PLAN_FROZEN_MUTATION_REFUSED"
REGISTRY_CHECK_UNKNOWN = "REGISTRY_CHECK_UNKNOWN"
FREEZE_BLOCKED_GAPS = "FREEZE_BLOCKED_GAPS"
PLAN_DRAFT_INVALID = "PLAN_DRAFT_INVALID"
PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
PLAN_INVALID = "PLAN_INVALID"
NOOP_FROZEN = "NOOP_FROZEN"
CHANGE_NOT_FOUND = "CHANGE_NOT_FOUND"
TASKS_INCOMPLETE = "TASKS_INCOMPLETE"


class BoundedVerifyPlanError(Exception):
    """Ошибка bounded verification plan authority."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlanDraft:
    """DRAFT plan после draft_plan."""

    change_id: str
    plan_id: str
    plan_status: str
    required_checks: tuple[Mapping[str, Any], ...]
    content_digest: str


@dataclass(frozen=True)
class FrozenPlan:
    """FROZEN plan после freeze_plan."""

    change_id: str
    plan_id: str
    plan_status: str
    plan_fingerprint: str
    required_checks: tuple[Mapping[str, Any], ...]
    content_digest: str
    noop: bool = False


def plan_carrier_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Путь к runtime carrier plan.json для change."""
    return (
        root / "openspec" / "changes" / change_id / "bounded-verify" / "plan.json"
    )


def change_dir(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Корень change в openspec/changes."""
    return root / "openspec" / "changes" / change_id


def compute_plan_fingerprint(frozen_subset: Mapping[str, Any]) -> str:
    """sha256(canonical_json_frozen_subset) — content identity без git HEAD."""
    digest = hashlib.sha256(canonical_json_bytes(frozen_subset)).hexdigest()
    return digest


def _load_plan_schema() -> Mapping[str, Any]:
    return json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_plan_document(doc: Mapping[str, Any]) -> None:
    """Минимальная schema validation по PLAN_SCHEMA.json (без jsonschema dep)."""
    required = (
        "schema_version",
        "change_id",
        "plan_id",
        "plan_status",
        "plan_fingerprint",
        "required_checks",
        "implementation_snapshot",
    )
    for key in required:
        if key not in doc:
            raise BoundedVerifyPlanError(PLAN_INVALID, f"missing field: {key}")

    schema_version = str(doc["schema_version"])
    if schema_version not in (PLAN_SCHEMA_VERSION, PLAN_SCHEMA_V2):
        raise BoundedVerifyPlanError(PLAN_INVALID, "invalid schema_version")

    if schema_version == PLAN_SCHEMA_V2:
        for v2_key in ("verification_profile", "verification_policy"):
            if v2_key not in doc:
                raise BoundedVerifyPlanError(PLAN_INVALID, f"missing field: {v2_key}")
        profile = str(doc["verification_profile"])
        policy = doc.get("verification_policy")
        if not isinstance(policy, dict):
            raise BoundedVerifyPlanError(PLAN_INVALID, "verification_policy invalid")
        if str(policy.get("verification_profile", "")) != profile:
            raise BoundedVerifyPlanError(PLAN_INVALID, "verification_policy profile mismatch")

    status = str(doc["plan_status"])
    fingerprint = str(doc["plan_fingerprint"])
    if status not in ("DRAFT", "FROZEN"):
        raise BoundedVerifyPlanError(PLAN_INVALID, "invalid plan_status")
    if status == "FROZEN":
        if len(fingerprint) != 64 or not all(c in "0123456789abcdef" for c in fingerprint):
            raise BoundedVerifyPlanError(PLAN_INVALID, "FROZEN requires valid fingerprint")
    elif fingerprint != "":
        raise BoundedVerifyPlanError(PLAN_INVALID, "DRAFT requires empty fingerprint")

    checks = doc["required_checks"]
    if not isinstance(checks, list) or not checks:
        raise BoundedVerifyPlanError(PLAN_INVALID, "required_checks must be non-empty")

    for item in checks:
        if not isinstance(item, dict):
            raise BoundedVerifyPlanError(PLAN_INVALID, "required_checks item invalid")
        if not str(item.get("check_id", "")).strip():
            raise BoundedVerifyPlanError(PLAN_INVALID, "check_id required")
        order = item.get("order")
        if not isinstance(order, int) or order < 1:
            raise BoundedVerifyPlanError(PLAN_INVALID, "order must be positive int")

    snapshot = doc.get("implementation_snapshot")
    if not isinstance(snapshot, dict):
        raise BoundedVerifyPlanError(PLAN_INVALID, "implementation_snapshot invalid")
    digest = str(snapshot.get("content_digest", "")).strip()
    if not digest:
        raise BoundedVerifyPlanError(PLAN_INVALID, "content_digest required")
    if schema_version == PLAN_SCHEMA_V2:
        role = snapshot.get("role_digests")
        if not isinstance(role, dict) or not role:
            raise BoundedVerifyPlanError(PLAN_INVALID, "v2 snapshot requires role_digests")


def _fingerprint_subset(doc: Mapping[str, Any]) -> dict[str, Any]:
    subset: dict[str, Any] = {
        "schema_version": doc["schema_version"],
        "change_id": doc["change_id"],
        "plan_id": doc["plan_id"],
        "plan_status": doc["plan_status"],
        "required_checks": doc["required_checks"],
        "implementation_snapshot": doc["implementation_snapshot"],
    }
    if str(doc.get("schema_version")) == PLAN_SCHEMA_V2:
        from bounded_verify_plan_profile import policy_identity_for_fingerprint

        policy = doc.get("verification_policy")
        if isinstance(policy, Mapping):
            subset["verification_profile"] = doc["verification_profile"]
            subset["verification_policy"] = policy_identity_for_fingerprint(policy)
    return subset


def _normalize_required_checks(
    required_checks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in required_checks:
        check_id = str(item["check_id"]).strip()
        order = int(item["order"])
        normalized.append({"check_id": check_id, "order": order})
    normalized.sort(key=lambda x: x["order"])
    return normalized


def _load_registry_check_ids(registry_path: Path) -> set[str]:
    if not registry_path.is_file():
        raise BoundedVerifyPlanError(REGISTRY_CHECK_UNKNOWN, "registry not found")
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoundedVerifyPlanError(REGISTRY_CHECK_UNKNOWN, "registry invalid")
    checks = data.get("checks")
    if not isinstance(checks, dict):
        raise BoundedVerifyPlanError(REGISTRY_CHECK_UNKNOWN, "registry checks missing")
    return {str(key) for key in checks}


def _validate_check_ids(
    required_checks: Sequence[Mapping[str, Any]],
    registry_path: Path,
    *,
    root: Path,
) -> None:
    from bounded_verify_s5_adapters import MANDATORY_S5_OBLIGATION_IDS, adapter_implemented

    known = _load_registry_check_ids(registry_path)
    for item in required_checks:
        check_id = str(item["check_id"]).strip()
        if check_id in MANDATORY_S5_OBLIGATION_IDS and not adapter_implemented(
            check_id, root=root
        ):
            raise BoundedVerifyPlanError(
                "PLAN_COVERAGE_INCOMPLETE",
                f"mandatory S5 adapter not machine-ready: {check_id}",
            )
        if check_id not in known and not adapter_implemented(check_id, root=root):
            raise BoundedVerifyPlanError(
                REGISTRY_CHECK_UNKNOWN,
                f"unknown check_id: {check_id}",
            )


def _implementation_snapshot_for_plan(
    change_id: str,
    *,
    root: Path,
    plan_schema: str,
) -> dict[str, Any]:
    """Snapshot bound to plan schema generation (v1 impl digest | v2 role digests)."""
    cdir = change_dir(change_id, root=root)
    if not cdir.is_dir():
        raise BoundedVerifyPlanError(CHANGE_NOT_FOUND, f"change not found: {change_id}")
    tasks_path = cdir / "tasks.md"
    if not tasks_path.is_file():
        raise BoundedVerifyPlanError(TASKS_INCOMPLETE, "tasks.md missing")
    if plan_schema == PLAN_SCHEMA_V2:
        from bounded_verify_snapshot import collect_v2_role_digests

        return collect_v2_role_digests(change_id, root=root)
    from bounded_verify_impl_completion import implementation_completion_digest

    digest = implementation_completion_digest(change_id, root=root)
    return {"content_digest": digest}


def _read_existing_plan(
    change_id: str,
    *,
    root: Path,
) -> Optional[MutableMapping[str, Any]]:
    path = plan_carrier_path(change_id, root=root)
    doc = read_canonical_json(path)
    if doc is None:
        return None
    try:
        _validate_plan_document(doc)
    except BoundedVerifyPlanError:
        raise
    return doc


def assert_plan_mutable(change_id: str, *, root: Path = REPO_ROOT) -> None:
    """Отказывает, если plan FROZEN (PLAN_FROZEN_MUTATION_REFUSED)."""
    doc = _read_existing_plan(change_id, root=root)
    if doc is None:
        return
    if doc.get("plan_status") == "FROZEN":
        raise BoundedVerifyPlanError(
            PLAN_FROZEN_MUTATION_REFUSED,
            "plan is FROZEN; mutation refused",
        )


def assert_frozen_plan_fingerprint_consistent(doc: Mapping[str, Any]) -> None:
    """Recompute fingerprint subset and reject tampered FROZEN carriers."""
    if str(doc.get("plan_status")) != "FROZEN":
        return
    expected = compute_plan_fingerprint(_fingerprint_subset(doc))
    stored = str(doc.get("plan_fingerprint", ""))
    if stored != expected:
        raise BoundedVerifyPlanError(
            PLAN_INVALID,
            "plan_fingerprint does not match canonical frozen content",
        )


def read_plan(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Читает canonical plan.json; отказывает если absent/invalid."""
    doc = _read_existing_plan(change_id, root=root)
    if doc is None:
        raise BoundedVerifyPlanError(PLAN_NOT_FOUND, "plan not found or invalid")
    assert_frozen_plan_fingerprint_consistent(doc)
    return dict(doc)


def draft_plan(
    change_id: str,
    required_checks: Sequence[Mapping[str, Any]],
    *,
    root: Path = REPO_ROOT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    verification_profile: str | None = None,
) -> PlanDraft:
    """Создаёт или обновляет DRAFT plan (sole supported write path до freeze).

    Args:
        change_id: OpenSpec change id.
        required_checks: Finite WHAT — ordered check_id list.
        root: Repo root for hermetic tests.
        registry_path: Registry for check_id validation.

    Returns:
        PlanDraft metadata.

    Raises:
        BoundedVerifyPlanError: frozen mutation, unknown check, invalid draft.
    """
    if not required_checks:
        raise BoundedVerifyPlanError(PLAN_DRAFT_INVALID, "required_checks empty")

    normalized = _normalize_required_checks(required_checks)
    _validate_check_ids(normalized, registry_path, root=root)

    plan_schema = PLAN_SCHEMA_VERSION
    verification_policy: dict[str, Any] | None = None
    if verification_profile is not None:
        from bounded_verify_plan_profile import (
            VERIFICATION_PROFILES,
            assert_freeze_mandatory_coverage,
            build_verification_policy,
        )

        if verification_profile not in VERIFICATION_PROFILES:
            raise BoundedVerifyPlanError(
                PLAN_DRAFT_INVALID,
                f"unknown verification_profile: {verification_profile}",
            )
        if verification_profile == "full_required":
            assert_freeze_mandatory_coverage(
                change_id,
                verification_profile,
                tuple(str(r["check_id"]) for r in normalized),
                root=root,
                registry_path=registry_path,
            )
        plan_schema = PLAN_SCHEMA_V2
        verification_policy = build_verification_policy(
            change_id,
            verification_profile,
            root=root,
            registry_path=registry_path,
        )

    snapshot = _implementation_snapshot_for_plan(
        change_id, root=root, plan_schema=plan_schema
    )
    content_digest = str(snapshot["content_digest"])

    existing = _read_existing_plan(change_id, root=root)
    if existing is not None:
        if existing.get("plan_status") == "FROZEN":
            raise BoundedVerifyPlanError(
                PLAN_FROZEN_MUTATION_REFUSED,
                "cannot draft over FROZEN plan",
            )
        plan_id = str(existing["plan_id"])
    else:
        plan_id = str(uuid.uuid4())

    document: dict[str, Any] = {
        "schema_version": plan_schema,
        "change_id": change_id,
        "plan_id": plan_id,
        "plan_status": "DRAFT",
        "plan_fingerprint": "",
        "required_checks": normalized,
        "implementation_snapshot": snapshot,
    }
    if verification_profile is not None and verification_policy is not None:
        document["verification_profile"] = verification_profile
        document["verification_policy"] = verification_policy
    _validate_plan_document(document)

    def _validate(doc: Mapping[str, Any]) -> None:
        _validate_plan_document(doc)

    atomic_publish_json(
        plan_carrier_path(change_id, root=root),
        document,
        validate=_validate,
    )

    return PlanDraft(
        change_id=change_id,
        plan_id=plan_id,
        plan_status="DRAFT",
        required_checks=tuple(normalized),
        content_digest=content_digest,
    )


def freeze_plan(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    check_executor: Optional[Callable[[str], None]] = None,
) -> FrozenPlan:
    """Замораживает DRAFT plan: FROZEN + plan_fingerprint + atomic publish.

    Не выполняет checks и не эмитит GO/NO_GO. ``check_executor`` только для
    тестов A04 (должен остаться не вызванным).

    Args:
        change_id: OpenSpec change id.
        root: Repo root.
        registry_path: Registry for validation.
        check_executor: Optional test probe — MUST NOT be invoked.

    Returns:
        FrozenPlan; ``noop=True`` при идемпотентном повторном freeze.

    Raises:
        BoundedVerifyPlanError: blocked gaps, unknown checks, invalid state.
    """
    if check_executor is not None:
        # Freeze never executes checks; executor is a test-only injection point.
        pass

    doc = _read_existing_plan(change_id, root=root)
    if doc is None:
        raise BoundedVerifyPlanError(FREEZE_BLOCKED_GAPS, "no draft plan to freeze")

    if doc.get("plan_status") == "FROZEN":
        _validate_plan_document(doc)
        return FrozenPlan(
            change_id=change_id,
            plan_id=str(doc["plan_id"]),
            plan_status="FROZEN",
            plan_fingerprint=str(doc["plan_fingerprint"]),
            required_checks=tuple(doc["required_checks"]),
            content_digest=str(doc["implementation_snapshot"]["content_digest"]),
            noop=True,
        )

    normalized = _normalize_required_checks(doc["required_checks"])
    _validate_check_ids(normalized, registry_path, root=root)
    from bounded_verify_impl_completion import assert_implementation_ready_for_freeze
    from bounded_verify_plan_profile import (
        assert_freeze_mandatory_coverage,
        plan_profile_from_document,
    )

    assert_implementation_ready_for_freeze(change_id, root=root)
    profile = plan_profile_from_document(doc)
    check_id_tuple = tuple(str(row["check_id"]) for row in normalized)
    if profile is not None:
        assert_freeze_mandatory_coverage(
            change_id,
            profile,
            check_id_tuple,
            root=root,
            registry_path=registry_path,
        )
    else:
        from bounded_verify_s5_obligation_applicability import (
            assert_plan_required_checks_match_conditional_policy,
        )

        assert_plan_required_checks_match_conditional_policy(
            change_id,
            check_id_tuple,
            root=root,
        )

    plan_schema = str(doc.get("schema_version", PLAN_SCHEMA_VERSION))
    snapshot = _implementation_snapshot_for_plan(
        change_id, root=root, plan_schema=plan_schema
    )
    content_digest = str(snapshot["content_digest"])

    try:
        sys_path = str((REPO_ROOT / ".cursor" / "pipeline" / "channel-impact").resolve())
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from channel_impact_guard import (  # noqa: PLC0415
            ChannelImpactGuardError,
            assert_planning_ready,
        )

        assert_planning_ready(change_id, root=root, phase="planning_freeze")
    except ChannelImpactGuardError as guard_error:
        raise BoundedVerifyPlanError(
            FREEZE_BLOCKED_GAPS,
            f"channel impact guard: {guard_error.code}: {guard_error.message}",
        ) from guard_error

    try:
        sys_path = str((REPO_ROOT / ".cursor" / "pipeline" / "channel-impact").resolve())
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from channel_impact_guard import (  # noqa: PLC0415
            ChannelImpactGuardError,
            assert_planning_ready,
        )

        assert_planning_ready(change_id, root=root, phase="planning_freeze")
    except ChannelImpactGuardError as guard_error:
        raise BoundedVerifyPlanError(
            FREEZE_BLOCKED_GAPS,
            f"channel impact guard: {guard_error.code}: {guard_error.message}",
        ) from guard_error

    frozen_doc: dict[str, Any] = {
        "schema_version": plan_schema,
        "change_id": change_id,
        "plan_id": str(doc["plan_id"]),
        "plan_status": "FROZEN",
        "required_checks": normalized,
        "implementation_snapshot": snapshot,
        "plan_fingerprint": "",
    }
    if profile is not None:
        from bounded_verify_plan_profile import build_verification_policy

        frozen_doc["verification_profile"] = profile
        frozen_doc["verification_policy"] = build_verification_policy(
            change_id, profile, root=root, registry_path=registry_path
        )
    subset = _fingerprint_subset(
        {**frozen_doc, "plan_status": "FROZEN"}
    )
    fingerprint = compute_plan_fingerprint(subset)
    frozen_doc["plan_fingerprint"] = fingerprint
    _validate_plan_document(frozen_doc)

    existing_frozen = _read_existing_plan(change_id, root=root)
    if (
        existing_frozen is not None
        and existing_frozen.get("plan_status") == "FROZEN"
        and existing_frozen.get("plan_fingerprint") == fingerprint
    ):
        return FrozenPlan(
            change_id=change_id,
            plan_id=str(existing_frozen["plan_id"]),
            plan_status="FROZEN",
            plan_fingerprint=fingerprint,
            required_checks=tuple(normalized),
            content_digest=content_digest,
            noop=True,
        )

    def _validate(payload: Mapping[str, Any]) -> None:
        _validate_plan_document(payload)
        if payload.get("plan_status") != "FROZEN":
            raise ValueError("freeze must publish FROZEN")

    atomic_publish_json(
        plan_carrier_path(change_id, root=root),
        frozen_doc,
        validate=_validate,
    )

    return FrozenPlan(
        change_id=change_id,
        plan_id=str(frozen_doc["plan_id"]),
        plan_status="FROZEN",
        plan_fingerprint=fingerprint,
        required_checks=tuple(normalized),
        content_digest=content_digest,
        noop=False,
    )
