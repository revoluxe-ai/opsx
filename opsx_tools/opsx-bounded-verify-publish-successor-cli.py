#!/usr/bin/env python3
"""Publish bounded-verify successor plan (stdin JSON) for post-implement recovery.

Distinguishes recommendation (inspect only), publish request without disk effect
(PLAN_NOT_FOUND / skipped), and committed supersession (SUCCESSOR_PLAN_CREATED).
Command strings and SUCCESSOR-PLAN.json metadata are not disk publication proof.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    BoundedVerifyPlanError,
    PLAN_NOT_FOUND,
    _read_existing_plan,
    read_plan,
)
from bounded_verify_supersession import (  # noqa: E402
    BoundedVerifyPlanError as SupersessionError,
    current_plan_pointer_path,
    generation_path,
    publish_successor_plan,
)

PUBLICATION_RECOMMENDED = "RECOMMENDED"
PUBLICATION_REQUESTED = "REQUESTED"
PUBLICATION_PUBLISHED = "PUBLISHED"

CODE_SUCCESSOR_PLAN_CREATED = "SUCCESSOR_PLAN_CREATED"
CODE_SUCCESSOR_PLAN_IDEMPOTENT = "SUCCESSOR_PLAN_IDEMPOTENT"


def _emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    raise SystemExit(exit_code)


def _bounded_dir(change_id: str, *, root: Path) -> Path:
    return root / "openspec" / "changes" / change_id / "bounded-verify"


def _disk_publication_proof(
    change_id: str,
    *,
    root: Path,
    old_plan_id: str,
    new_plan_id: str,
    generation: int,
) -> dict[str, Any]:
    archive_plan = (
        _bounded_dir(change_id, root=root)
        / "archive"
        / old_plan_id
        / "plan.json"
    )
    return {
        "predecessor_plan_id": old_plan_id,
        "carrier_plan_id": new_plan_id,
        "generation": generation,
        "predecessor_archive_plan_path": str(archive_plan) if archive_plan.is_file() else None,
        "current_plan_pointer_path": str(current_plan_pointer_path(change_id, root=root)),
        "generation_path": str(generation_path(change_id, root=root)),
    }


def _recommend_payload(change_id: str, *, root: Path) -> dict[str, Any]:
    doc = _read_existing_plan(change_id, root=root)
    frozen = doc is not None and str(doc.get("plan_status")) == "FROZEN"
    binding: dict[str, Any] = {
        "has_bounded_verify_plan": doc is not None,
        "plan_status": doc.get("plan_status") if doc else None,
        "plan_id": doc.get("plan_id") if doc else None,
        "plan_fingerprint": doc.get("plan_fingerprint") if doc else None,
    }
    return {
        "ok": True,
        "action": "recommend",
        "publication_state": PUBLICATION_RECOMMENDED,
        "change_id": change_id,
        "frozen_plan_present": frozen,
        "bounded_verify_binding": binding,
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        _emit({"ok": False, "code": "INVALID_STDIN_JSON"}, 1)

    change_id = str(payload.get("change_id") or "")
    root = Path(payload.get("root") or REPO_ROOT)
    action = str(payload.get("action") or "publish").lower()
    if not change_id:
        _emit({"ok": False, "code": "CHANGE_ID_MISSING"}, 1)

    if action in ("recommend", "recommended"):
        _emit(_recommend_payload(change_id, root=root))

    checks = payload.get("required_checks")
    registry_raw = payload.get("registry_path")
    registry = Path(registry_raw) if registry_raw else DEFAULT_REGISTRY_PATH

    try:
        plan = read_plan(change_id, root=root)
    except BoundedVerifyPlanError as exc:
        if exc.code == PLAN_NOT_FOUND:
            _emit(
                {
                    "ok": True,
                    "action": "publish",
                    "skipped": True,
                    "publication_state": PUBLICATION_REQUESTED,
                    "reason": PLAN_NOT_FOUND,
                },
            )
        _emit(
            {
                "ok": False,
                "code": exc.code,
                "detail": str(exc),
                "publication_state": PUBLICATION_REQUESTED,
            },
            1,
        )

    if str(plan.get("plan_status") or "") != "FROZEN":
        _emit(
            {
                "ok": False,
                "code": "SUPERSESSION_NO_FROZEN",
                "plan_status": plan.get("plan_status"),
                "publication_state": PUBLICATION_REQUESTED,
            },
            1,
        )

    resolved_checks = checks if checks is not None else plan.get("required_checks") or []
    try:
        result = publish_successor_plan(
            change_id,
            resolved_checks,
            root=root,
            registry_path=registry.resolve(),
            expected_old_plan_id=payload.get("expected_old_plan_id"),
        )
    except SupersessionError as exc:
        _emit(
            {
                "ok": False,
                "code": exc.code,
                "detail": str(exc),
                "publication_state": PUBLICATION_REQUESTED,
            },
            1,
        )
    except Exception as exc:  # noqa: BLE001
        _emit(
            {
                "ok": False,
                "code": "PUBLISH_SUCCESSOR_FAILED",
                "detail": str(exc),
                "publication_state": PUBLICATION_REQUESTED,
            },
            1,
        )

    new_plan_id = str(result.new_plan_id or "")
    if not new_plan_id:
        _emit(
            {
                "ok": False,
                "code": "SUCCESSOR_PLAN_ID_MISSING",
                "publication_state": PUBLICATION_REQUESTED,
            },
            1,
        )

    if result.noop:
        proof = _disk_publication_proof(
            change_id,
            root=root,
            old_plan_id=str(result.old_plan_id),
            new_plan_id=new_plan_id,
            generation=int(result.generation),
        )
        _emit(
            {
                "ok": True,
                "action": "publish",
                "skipped": False,
                "noop": True,
                "publication_state": PUBLICATION_PUBLISHED,
                "publication_code": CODE_SUCCESSOR_PLAN_IDEMPOTENT,
                "old_plan_id": result.old_plan_id,
                "new_plan_id": new_plan_id,
                "plan_fingerprint": result.plan_fingerprint,
                "generation": result.generation,
                "disk_proof": proof,
            },
        )

    proof = _disk_publication_proof(
        change_id,
        root=root,
        old_plan_id=str(result.old_plan_id),
        new_plan_id=new_plan_id,
        generation=int(result.generation),
    )
    if proof.get("predecessor_archive_plan_path") is None:
        _emit(
            {
                "ok": False,
                "code": "DISK_PROOF_INCOMPLETE",
                "publication_state": PUBLICATION_REQUESTED,
                "old_plan_id": result.old_plan_id,
                "new_plan_id": new_plan_id,
            },
            1,
        )

    _emit(
        {
            "ok": True,
            "action": "publish",
            "skipped": False,
            "noop": False,
            "publication_state": PUBLICATION_PUBLISHED,
            "publication_code": CODE_SUCCESSOR_PLAN_CREATED,
            "old_plan_id": result.old_plan_id,
            "new_plan_id": new_plan_id,
            "plan_fingerprint": result.plan_fingerprint,
            "content_digest": result.content_digest,
            "generation": result.generation,
            "disk_proof": proof,
        },
    )


if __name__ == "__main__":
    main()
