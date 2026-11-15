"""Негативные contract-тесты MINIMAL_AUTHORITY_GRANT_V0 (OPSX-2 S0+S1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from conftest import VALIDATION_NOW
from minimal_authority_grant import (
    GrantRejectReason,
    InvocationSurface,
    validate_minimal_authority_grant_v0,
)


def _base_grant(**overrides: Any) -> dict[str, Any]:
    """Минимально валидный grant для позитивного пути validation_only."""

    grant: dict[str, Any] = {
        "schema": "MINIMAL_AUTHORITY_GRANT_V0",
        "grant_id": "mag-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "issuer": "OWNER",
        "issued_at": "2026-10-13T08:00:00Z",
        "expires_at": "2026-10-14T08:00:00Z",
        "subject": {
            "agent_id": "bc-00f84b95-540f-590e-b857-81003c8927f0",
            "role": "executor",
        },
        "repo": "revoluxe-ai/stroy-system",
        "change_id": "opsx-2-execution-authority-boundary-v0",
        "actions": ["local_commit"],
        "model_tier": "inherit",
        "owner_fast_allow": False,
        "exact_token": "GO",
        "on_conflict": "OWNER_ATTENTION",
        "forbids": [
            "force_push",
            "push_unlisted_oid",
            "merge",
            "deploy",
            "migrate",
            "semantic_write_guess",
        ],
        "owner_attestation_ref": "store:bc-c9e9c005/owner-go-opsx-2",
    }
    grant.update(overrides)
    return grant


def test_malformed_grant_missing_schema() -> None:
    grant = _base_grant()
    del grant["schema"]
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert not result.ok
    assert result.reason == GrantRejectReason.MALFORMED


def test_now_omitted_rejected_e1() -> None:
    grant = _base_grant()
    result = validate_minimal_authority_grant_v0(grant)
    assert not result.ok
    assert result.reason == GrantRejectReason.MALFORMED
    assert "now is required" in (result.detail or "")


def test_worker_mint_rejected() -> None:
    grant = _base_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        mint_channel="worker",
        now=VALIDATION_NOW,
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.WORKER_MINT


def test_grant_cannot_override_supervisor_section_27() -> None:
    grant = _base_grant(
        actions=["push_ref"],
        base_oid="ed104faa57864f18ecb4621e3c5085c3e1f67f54",
        tip_oid="f7d843452e74f8256ffa50f0355ddcc8aa85d3a5",
        ref="refs/heads/cursor/invest-yalta-mvp-integration-5d01",
    )
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.SUPERVISOR_SUBAGENT,
        now=VALIDATION_NOW,
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.STABLE_POLICY_OVERRIDES_GRANT


def test_model_tier_standard_rejected() -> None:
    grant = _base_grant(model_tier="standard")
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert not result.ok
    assert result.reason == GrantRejectReason.MODEL_TIER_UNDEFINED


def test_local_commit_does_not_require_tip_oid() -> None:
    grant = _base_grant(actions=["local_commit"])
    assert "tip_oid" not in grant
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert result.ok


def test_push_ref_requires_tip_oid() -> None:
    grant = _base_grant(
        actions=["push_ref"],
        base_oid="ed104faa57864f18ecb4621e3c5085c3e1f67f54",
        ref="refs/heads/cursor/test-771f",
    )
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert not result.ok
    assert result.reason == GrantRejectReason.PUBLICATION_OID_REQUIRED


def test_action_forbid_collision() -> None:
    grant = _base_grant(actions=["local_commit"], forbids=["force_push", "deploy"])
    grant["actions"] = ["merge"]
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert not result.ok
    assert result.reason == GrantRejectReason.MALFORMED


def test_expired_grant() -> None:
    grant = _base_grant(
        expires_at="2026-10-12T08:00:00Z",
        issued_at="2026-10-11T08:00:00Z",
    )
    result = validate_minimal_authority_grant_v0(
        grant,
        now=datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.EXPIRED


def test_invalid_attestation_ref() -> None:
    grant = _base_grant(owner_attestation_ref="workspace/opsx/grants/forged.json")
    result = validate_minimal_authority_grant_v0(grant, now=VALIDATION_NOW)
    assert not result.ok
    assert result.reason == GrantRejectReason.ATTESTATION_INVALID
