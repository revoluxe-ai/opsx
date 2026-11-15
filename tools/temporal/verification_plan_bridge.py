"""Bridge Temporal verification preflight to bounded-verify plan carrier.

Authoritative runtime plan: ``openspec/changes/<change>/bounded-verify/plan.json``
(written only via ``bounded_verify_plan``). Legacy ``verify/VERIFICATION-PLAN.json``
is not copied or mirrored; readers resolve the bounded carrier directly.

VERIFICATION_SESSION_DEPENDENCY: NONE
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_GAME_ROOT = Path(__file__).resolve().parents[2]
_BOUNDED_VERIFY_ROOT = _GAME_ROOT / ".cursor" / "pipeline" / "bounded-verify"


def _ensure_bounded_verify_import_path() -> None:
    path = str(_BOUNDED_VERIFY_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def bounded_plan_carrier_path(repo: Path | str, change: str) -> Path:
    """Canonical bounded-verify plan carrier path for ``change``."""
    return (
        Path(repo)
        / "openspec"
        / "changes"
        / change
        / "bounded-verify"
        / "plan.json"
    )


def bounded_ledger_carrier_path(repo: Path | str, change: str) -> Path:
    """Canonical bounded-verify ledger carrier path for ``change``."""
    return (
        Path(repo)
        / "openspec"
        / "changes"
        / change
        / "bounded-verify"
        / "ledger.json"
    )


@dataclass(frozen=True)
class VerificationPlanContext:
    """Resolved verification plan + optional ledger binding for Temporal preflight."""

    status: str
    change: str
    plan_id: str | None
    plan_fingerprint: str | None
    plan_carrier_path: str
    ledger_carrier_path: str
    ledger_present: bool
    ledger_revision: int | None
    revision_binding_ok: bool | None
    plan_status: str | None
    block_reason: str | None
    message: str | None
    carrier: str = "bounded-verify/plan.json"

    def to_activity_payload(self) -> dict[str, Any]:
        """Shape returned by ``ensure_verification_context``."""
        body: dict[str, Any] = {
            "status": self.status,
            "change": self.change,
            "plan_id": self.plan_id,
            "plan_fingerprint": self.plan_fingerprint,
            "plan_carrier_path": self.plan_carrier_path,
            "ledger_carrier_path": self.ledger_carrier_path,
            "ledger_path": self.ledger_carrier_path if self.ledger_present else None,
            "ledger_present": self.ledger_present,
            "ledger_revision": self.ledger_revision,
            "revision_binding_ok": self.revision_binding_ok,
            "plan_status": self.plan_status,
            "VERIFICATION_PLAN_CARRIER": self.carrier,
            "VERIFICATION_SESSION_DEPENDENCY": "NONE",
        }
        if self.block_reason:
            body["block_reason"] = self.block_reason
        if self.message:
            body["message"] = self.message
        return body


def _load_plan_via_authority(repo: Path, change: str) -> dict[str, Any]:
    _ensure_bounded_verify_import_path()
    from bounded_verify_plan import (  # noqa: WPS433
        BoundedVerifyPlanError,
        read_plan,
    )

    try:
        return read_plan(change, root=repo)
    except BoundedVerifyPlanError as exc:
        if exc.code == "PLAN_NOT_FOUND":
            raise FileNotFoundError(str(exc)) from exc
        raise ValueError(f"{exc.code}: {exc.message}") from exc


def _load_ledger_optional(repo: Path, change: str) -> dict[str, Any] | None:
    path = bounded_ledger_carrier_path(repo, change)
    if not path.is_file():
        return None
    _ensure_bounded_verify_import_path()
    from bounded_verify_ledger import (  # noqa: WPS433
        BoundedVerifyLedgerError,
        read_ledger,
    )

    try:
        return read_ledger(change, root=repo)
    except BoundedVerifyLedgerError as exc:
        raise ValueError(f"{exc.code}: {exc.message}") from exc


def resolve_verification_plan_context(
    repo: Path | str,
    change: str,
) -> VerificationPlanContext:
    """Resolve bounded-verify plan for Temporal preflight (no legacy carrier copy)."""
    root = Path(repo)
    change_id = change.strip()
    plan_path = bounded_plan_carrier_path(root, change_id)
    ledger_path = bounded_ledger_carrier_path(root, change_id)

    if not plan_path.is_file():
        return VerificationPlanContext(
            status="BLOCKED",
            change=change_id,
            plan_id=None,
            plan_fingerprint=None,
            plan_carrier_path=str(plan_path),
            ledger_carrier_path=str(ledger_path),
            ledger_present=ledger_path.is_file(),
            ledger_revision=None,
            revision_binding_ok=None,
            plan_status=None,
            block_reason="VERIFICATION_PLAN_MISSING",
            message=f"Missing bounded-verify plan for change={change_id}",
        )

    try:
        plan = _load_plan_via_authority(root, change_id)
    except FileNotFoundError:
        return VerificationPlanContext(
            status="BLOCKED",
            change=change_id,
            plan_id=None,
            plan_fingerprint=None,
            plan_carrier_path=str(plan_path),
            ledger_carrier_path=str(ledger_path),
            ledger_present=ledger_path.is_file(),
            ledger_revision=None,
            revision_binding_ok=None,
            plan_status=None,
            block_reason="VERIFICATION_PLAN_MISSING",
            message=f"Plan carrier absent or invalid for change={change_id}",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return VerificationPlanContext(
            status="CONTRACT_ERROR",
            change=change_id,
            plan_id=None,
            plan_fingerprint=None,
            plan_carrier_path=str(plan_path),
            ledger_carrier_path=str(ledger_path),
            ledger_present=ledger_path.is_file(),
            ledger_revision=None,
            revision_binding_ok=None,
            plan_status=None,
            block_reason="PLAN_JSON_INVALID",
            message=str(exc),
        )

    plan_id = str(plan.get("plan_id") or "")
    plan_status = str(plan.get("plan_status") or "")
    plan_fingerprint = str(plan.get("plan_fingerprint") or "")

    if plan_status != "FROZEN":
        return VerificationPlanContext(
            status="BLOCKED",
            change=change_id,
            plan_id=plan_id or None,
            plan_fingerprint=plan_fingerprint or None,
            plan_carrier_path=str(plan_path),
            ledger_carrier_path=str(ledger_path),
            ledger_present=ledger_path.is_file(),
            ledger_revision=None,
            revision_binding_ok=None,
            plan_status=plan_status or None,
            block_reason="PLAN_NOT_FROZEN",
            message=f"plan_status={plan_status!r} (need FROZEN)",
        )

    if len(plan_fingerprint) != 64:
        return VerificationPlanContext(
            status="CONTRACT_ERROR",
            change=change_id,
            plan_id=plan_id or None,
            plan_fingerprint=None,
            plan_carrier_path=str(plan_path),
            ledger_carrier_path=str(ledger_path),
            ledger_present=ledger_path.is_file(),
            ledger_revision=None,
            revision_binding_ok=None,
            plan_status=plan_status,
            block_reason="PLAN_FINGERPRINT_INVALID",
            message="FROZEN plan requires 64-char plan_fingerprint",
        )

    ledger_revision: int | None = None
    revision_binding_ok: bool | None = None
    ledger_present = ledger_path.is_file()
    if ledger_present:
        try:
            ledger = _load_ledger_optional(root, change_id)
        except ValueError as exc:
            return VerificationPlanContext(
                status="CONTRACT_ERROR",
                change=change_id,
                plan_id=plan_id or None,
                plan_fingerprint=plan_fingerprint,
                plan_carrier_path=str(plan_path),
                ledger_carrier_path=str(ledger_path),
                ledger_present=True,
                ledger_revision=None,
                revision_binding_ok=False,
                plan_status=plan_status,
                block_reason="LEDGER_INVALID",
                message=str(exc),
            )
        if ledger is not None:
            ledger_revision = int(ledger.get("ledger_revision") or 0)
            revision_binding_ok = (
                ledger.get("plan_id") == plan_id
                and ledger.get("plan_fingerprint") == plan_fingerprint
            )
            if not revision_binding_ok:
                return VerificationPlanContext(
                    status="BLOCKED",
                    change=change_id,
                    plan_id=plan_id or None,
                    plan_fingerprint=plan_fingerprint,
                    plan_carrier_path=str(plan_path),
                    ledger_carrier_path=str(ledger_path),
                    ledger_present=True,
                    ledger_revision=ledger_revision,
                    revision_binding_ok=False,
                    plan_status=plan_status,
                    block_reason="LEDGER_STALE_PLAN_IDENTITY",
                    message="ledger plan_id/plan_fingerprint mismatch frozen plan",
                )

    return VerificationPlanContext(
        status="OK",
        change=change_id,
        plan_id=plan_id or None,
        plan_fingerprint=plan_fingerprint,
        plan_carrier_path=str(plan_path),
        ledger_carrier_path=str(ledger_path),
        ledger_present=ledger_present,
        ledger_revision=ledger_revision,
        revision_binding_ok=revision_binding_ok,
        plan_status=plan_status,
        block_reason=None,
        message=None,
    )
