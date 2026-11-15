# -*- coding: utf-8 -*-
"""Versioned supersession для FROZEN bounded-verify plans (crash-safe publish)."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from bounded_verify_atomic import atomic_publish_json, read_canonical_json
from bounded_verify_ledger import read_ledger, scheduler_lock_path, _scheduler_lock
from bounded_verify_plan import (
    BoundedVerifyPlanError,
    PLAN_NOT_FOUND,
    _fingerprint_subset,
    _normalize_required_checks,
    _validate_check_ids,
    compute_plan_fingerprint,
    plan_carrier_path,
    DEFAULT_REGISTRY_PATH,
)
from bounded_verify_snapshot import PLAN_SCHEMA_V2, collect_v2_role_digests

REPO_ROOT = Path(__file__).resolve().parents[3]

SUPERSESSION_ARCHIVE_FAILED = "SUPERSESSION_ARCHIVE_FAILED"
SUPERSESSION_NO_FROZEN = "SUPERSESSION_NO_FROZEN"
SUPERSESSION_OUTSTANDING_ASSIGNMENTS = "SUPERSESSION_OUTSTANDING_ASSIGNMENTS"
SUPERSESSION_CAS_MISMATCH = "SUPERSESSION_CAS_MISMATCH"
SUPERSESSION_PENDING = "SUPERSESSION_PENDING"


@dataclass(frozen=True)
class SuccessorPlanResult:
    """Опубликованный successor FROZEN plan."""

    change_id: str
    old_plan_id: str
    new_plan_id: str
    plan_fingerprint: str
    content_digest: str
    generation: int
    noop: bool = False


def _bounded_dir(change_id: str, *, root: Path) -> Path:
    return root / "openspec" / "changes" / change_id / "bounded-verify"


def current_plan_pointer_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    return _bounded_dir(change_id, root=root) / "CURRENT_PLAN.json"


def generation_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    return _bounded_dir(change_id, root=root) / "GENERATION.json"


def pending_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    return _bounded_dir(change_id, root=root) / ".supersession-pending.json"


def _archive_immutable(change_id: str, plan_doc: Mapping[str, Any], *, root: Path) -> Path:
    plan_id = str(plan_doc["plan_id"])
    archive_dir = _bounded_dir(change_id, root=root) / "archive" / plan_id
    if archive_dir.is_dir() and (archive_dir / "plan.json").is_file():
        return archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    plan_dst = archive_dir / "plan.json"
    if plan_dst.exists():
        raise BoundedVerifyPlanError(
            SUPERSESSION_ARCHIVE_FAILED,
            f"archive already exists for plan_id={plan_id}",
        )
    ledger_src = _bounded_dir(change_id, root=root) / "ledger.json"
    ledger_dst = archive_dir / "ledger.json"
    try:
        shutil.copy2(plan_carrier_path(change_id, root=root), plan_dst)
        if ledger_src.is_file():
            shutil.copy2(ledger_src, ledger_dst)
    except OSError as exc:
        raise BoundedVerifyPlanError(
            SUPERSESSION_ARCHIVE_FAILED,
            f"archive copy failed: {exc}",
        ) from exc
    sidecar = {
        "superseded_plan_id": plan_id,
        "superseded_fingerprint": plan_doc.get("plan_fingerprint"),
        "status": "ARCHIVED_IMMUTABLE",
    }
    sidecar_path = archive_dir / "SUPERSEDED.json"
    if not sidecar_path.exists():
        sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return archive_dir


def _fresh_ledger_document(
    change_id: str,
    plan_id: str,
    plan_fingerprint: str,
    required_checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    entries = [
        {
            "check_id": row["check_id"],
            "order": row["order"],
            "state": "PENDING",
            "assignment_epoch": 0,
            "last_result_id": None,
        }
        for row in required_checks
    ]
    return {
        "schema_version": "bounded-verify-ledger-v1",
        "change_id": change_id,
        "ledger_id": str(uuid.uuid4()),
        "plan_id": plan_id,
        "plan_fingerprint": plan_fingerprint,
        "ledger_revision": 1,
        "ledger_status": "ACTIVE",
        "entries": entries,
        "current_assignment": None,
    }


def _read_generation(change_id: str, *, root: Path) -> dict[str, Any]:
    doc = read_canonical_json(generation_path(change_id, root=root))
    return dict(doc) if doc else {"generation": 0}


def _assert_no_outstanding_assignments(change_id: str, *, root: Path) -> None:
    try:
        ledger = read_ledger(change_id, root=root)
    except Exception:
        return
    if ledger.get("current_assignment"):
        raise BoundedVerifyPlanError(
            SUPERSESSION_OUTSTANDING_ASSIGNMENTS,
            "ledger has unresolved current_assignment",
        )
    for entry in ledger.get("entries", []):
        if entry.get("state") == "CURRENT":
            raise BoundedVerifyPlanError(
                SUPERSESSION_OUTSTANDING_ASSIGNMENTS,
                "ledger has CURRENT entry",
            )


def _maybe_crash(hook: str) -> None:
    if os.environ.get("BOUNDED_VERIFY_SUPERSESSION_CRASH") == hook:
        raise RuntimeError(f"injected crash at {hook}")


def publish_successor_plan(
    change_id: str,
    required_checks: Sequence[Mapping[str, Any]],
    *,
    root: Path = REPO_ROOT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    expected_old_plan_id: str | None = None,
) -> SuccessorPlanResult:
    """Crash-safe supersession: archive → publish plan/ledger/generation atomically from consumer view."""
    lock_path = scheduler_lock_path(change_id, root=root)
    with _scheduler_lock(lock_path):
        existing = read_canonical_json(plan_carrier_path(change_id, root=root))
        if existing is None:
            raise BoundedVerifyPlanError(PLAN_NOT_FOUND, "no plan to supersede")
        if existing.get("plan_status") != "FROZEN":
            raise BoundedVerifyPlanError(SUPERSESSION_NO_FROZEN, "current plan is not FROZEN")

        old_plan_id = str(existing["plan_id"])
        if expected_old_plan_id and expected_old_plan_id != old_plan_id:
            raise BoundedVerifyPlanError(
                SUPERSESSION_CAS_MISMATCH,
                f"expected old_plan_id {expected_old_plan_id}, got {old_plan_id}",
            )

        _assert_no_outstanding_assignments(change_id, root=root)

        normalized = _normalize_required_checks(required_checks)
        _validate_check_ids(normalized, registry_path, root=root)
        snapshot = collect_v2_role_digests(change_id, root=root)
        resume = read_canonical_json(pending_path(change_id, root=root)) or {}
        new_plan_id = str(resume.get("new_plan_id") or uuid.uuid4())
        frozen_doc: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA_V2,
            "change_id": change_id,
            "plan_id": new_plan_id,
            "plan_status": "FROZEN",
            "required_checks": normalized,
            "implementation_snapshot": snapshot,
            "plan_fingerprint": "",
        }
        from bounded_verify_plan_profile import (
            build_verification_policy,
            plan_profile_from_document,
        )

        profile = plan_profile_from_document(existing) or "bounded_minimal"
        policy = existing.get("verification_policy")
        if not isinstance(policy, dict) or str(policy.get("verification_profile", "")) != profile:
            policy = build_verification_policy(
                change_id,
                profile,
                root=root,
                registry_path=registry_path,
            )
        frozen_doc["verification_profile"] = profile
        frozen_doc["verification_policy"] = policy
        subset = _fingerprint_subset({**frozen_doc, "plan_status": "FROZEN"})
        fingerprint = compute_plan_fingerprint(subset)
        frozen_doc["plan_fingerprint"] = fingerprint

        if (
            str(existing.get("plan_fingerprint", "")) == fingerprint
            and tuple(existing.get("required_checks", [])) == tuple(normalized)
        ):
            gen = _read_generation(change_id, root=root)
            return SuccessorPlanResult(
                change_id=change_id,
                old_plan_id=old_plan_id,
                new_plan_id=old_plan_id,
                plan_fingerprint=str(existing["plan_fingerprint"]),
                content_digest=str(existing["implementation_snapshot"]["content_digest"]),
                generation=int(gen.get("generation", 0)),
                noop=True,
            )

        pending = read_canonical_json(pending_path(change_id, root=root)) or {}
        if pending.get("status") == "COMMITTED" and pending.get("new_plan_id"):
            gen = _read_generation(change_id, root=root)
            return SuccessorPlanResult(
                change_id=change_id,
                old_plan_id=str(pending.get("old_plan_id", old_plan_id)),
                new_plan_id=str(pending["new_plan_id"]),
                plan_fingerprint=str(pending.get("plan_fingerprint", fingerprint)),
                content_digest=str(pending.get("content_digest", snapshot["content_digest"])),
                generation=int(gen.get("generation", 0)),
                noop=True,
            )

        pending_doc = {
            "status": str(resume.get("status", "NEW")),
            "old_plan_id": old_plan_id,
            "new_plan_id": new_plan_id,
            "plan_fingerprint": fingerprint,
            "content_digest": snapshot["content_digest"],
        }
        if pending_doc["status"] not in ("ARCHIVED", "PLAN_PUBLISHED", "LEDGER_PUBLISHED", "COMMITTED"):
            _maybe_crash("before_archive")
            _archive_immutable(change_id, existing, root=root)
            pending_doc["status"] = "ARCHIVED"
            atomic_publish_json(pending_path(change_id, root=root), pending_doc, validate=lambda _: None)

        def _validate(doc: Mapping[str, Any]) -> None:
            if doc.get("plan_status") != "FROZEN":
                raise ValueError("successor must be FROZEN")

        if pending_doc["status"] not in ("PLAN_PUBLISHED", "LEDGER_PUBLISHED", "COMMITTED"):
            _maybe_crash("before_plan")
            atomic_publish_json(plan_carrier_path(change_id, root=root), frozen_doc, validate=_validate)
            pending_doc["status"] = "PLAN_PUBLISHED"
            atomic_publish_json(pending_path(change_id, root=root), pending_doc, validate=lambda _: None)

        if pending_doc["status"] not in ("LEDGER_PUBLISHED", "COMMITTED"):
            _maybe_crash("before_ledger")
            ledger_doc = _fresh_ledger_document(change_id, new_plan_id, fingerprint, normalized)
            atomic_publish_json(
                _bounded_dir(change_id, root=root) / "ledger.json",
                ledger_doc,
                validate=lambda _: None,
            )
            pending_doc["status"] = "LEDGER_PUBLISHED"
            atomic_publish_json(pending_path(change_id, root=root), pending_doc, validate=lambda _: None)

        gen_doc = _read_generation(change_id, root=root)
        next_gen = int(gen_doc.get("generation", 0)) + 1
        pointer = {
            "generation": next_gen,
            "plan_id": new_plan_id,
            "plan_fingerprint": fingerprint,
            "content_digest": snapshot["content_digest"],
            "supersedes_plan_id": old_plan_id,
        }
        _maybe_crash("before_generation")
        atomic_publish_json(current_plan_pointer_path(change_id, root=root), pointer, validate=lambda _: None)
        atomic_publish_json(
            generation_path(change_id, root=root),
            {
                "generation": next_gen,
                "plan_id": new_plan_id,
                "plan_fingerprint": fingerprint,
                "supersedes_plan_id": old_plan_id,
            },
            validate=lambda _: None,
        )
        pending_doc["status"] = "COMMITTED"
        atomic_publish_json(pending_path(change_id, root=root), pending_doc, validate=lambda _: None)

        return SuccessorPlanResult(
            change_id=change_id,
            old_plan_id=old_plan_id,
            new_plan_id=new_plan_id,
            plan_fingerprint=fingerprint,
            content_digest=str(snapshot["content_digest"]),
            generation=next_gen,
            noop=False,
        )
