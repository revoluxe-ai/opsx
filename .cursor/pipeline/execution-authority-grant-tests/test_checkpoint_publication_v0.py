"""Contract-тесты checkpoint_publish (OPSX-2 S1.5)."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import VALIDATION_NOW, gate_derived_invocation
from checkpoint_publication_v0 import (
    CheckpointPhase,
    CheckpointPublishContext,
    CheckpointRejectReason,
    FORBIDDEN_SUCCESS_WORDS,
    transition_checkpoint_publication,
)
from minimal_authority_grant import (
    GrantRejectReason,
    InvocationSurface,
    validate_minimal_authority_grant_v0,
)

_AGENT = "bc-00f84b95-540f-590e-b857-81003c8927f0"
_FEATURE_REF = "refs/heads/cursor/opsx-2-feature-771f"
_BASE_OID = "ed104faa57864f18ecb4621e3c5085c3e1f67f54"
_TIP_OID = "f7d843452e74f8256ffa50f0355ddcc8aa85d3a5"
_REMOTE_OID = "c6e3e7855555555555555555555555555555555"
_EXEC = "exec-opsx-2-001"


def _checkpoint_grant(**overrides: Any) -> dict[str, Any]:
    grant: dict[str, Any] = {
        "schema": "MINIMAL_AUTHORITY_GRANT_V0",
        "grant_id": "mag-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "issuer": "OWNER",
        "issued_at": "2026-10-13T08:00:00Z",
        "expires_at": "2026-10-14T08:00:00Z",
        "subject": {"agent_id": _AGENT, "role": "executor"},
        "repo": "revoluxe-ai/stroy-system",
        "change_id": "opsx-2-execution-authority-boundary-v0",
        "actions": ["checkpoint_publish"],
        "ref": _FEATURE_REF,
        "base_oid": _BASE_OID,
        "execution_id": _EXEC,
        "grant_revision": 1,
        "checkpoint": {
            "mode": "auto_commit_and_push",
            "extra_go": False,
            "idempotency": "git_ff_oid",
        },
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


def _ctx(**overrides: Any) -> CheckpointPublishContext:
    gate = overrides.pop("gate", gate_derived_invocation())
    base = {
        "surface": InvocationSurface.CLOUD_PARENT,
        "agent_id": _AGENT,
        "execution_id": _EXEC,
        "grant_revision": 1,
        "grant_ref": _FEATURE_REF,
        "remote_ref_target": _FEATURE_REF,
        "head_ref": _FEATURE_REF,
        "repo_remote": "https://github.com/revoluxe-ai/stroy-system.git",
        "gate": gate,
    }
    base.update(overrides)
    return CheckpointPublishContext(**base)


@pytest.mark.parametrize(
    "protected_ref",
    [
        "refs/heads/main",
        "refs/heads/master",
        "refs/heads/stage",
    ],
)
def test_reject_default_protected_refs_on_grant(protected_ref: str) -> None:
    grant = _checkpoint_grant(ref=protected_ref)
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason in {
        GrantRejectReason.PROTECTED_REF,
        GrantRejectReason.MALFORMED,
    }


def test_reject_owner_configured_prohibited_ref() -> None:
    grant = _checkpoint_grant(
        ref="refs/heads/cursor/chore-internal-771f",
        owner_prohibited_refs=["cursor/chore-internal-771f"],
    )
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.PROTECTED_REF


def test_reject_alternate_remote_target() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(remote_ref_target="refs/heads/cursor/other-branch-771f"),
    )
    assert not result.ok
    assert result.reason == CheckpointRejectReason.ALTERNATE_TARGET


def test_reject_worker_minted_checkpoint_grant() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        mint_channel="worker",
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.WORKER_MINT


def test_reject_cross_execution_grant() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(execution_id="exec-other"),
    )
    assert not result.ok
    assert result.reason == CheckpointRejectReason.CROSS_EXECUTION


def test_reject_stale_grant_revision() -> None:
    grant = _checkpoint_grant(grant_revision=2)
    result = transition_checkpoint_publication(
        grant,
        _ctx(grant_revision=1),
    )
    assert not result.ok
    assert result.reason == CheckpointRejectReason.STALE_REVISION


def test_reject_unauthorized_invocation_surface_opsx_commit() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.OPSX_COMMIT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(surface=InvocationSurface.OPSX_COMMIT),
    )
    assert not result.ok
    assert "CHECKPOINT_REJECTED_UNAUTHORIZED_SURFACE" in str(result.reason)


def test_reject_supervisor_subagent_checkpoint() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.SUPERVISOR_SUBAGENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(surface=InvocationSurface.SUPERVISOR_SUBAGENT),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.STABLE_POLICY_OVERRIDES_GRANT


def test_reject_force_push_and_ref_delete() -> None:
    grant = _checkpoint_grant()
    force = transition_checkpoint_publication(grant, _ctx(force_push=True))
    assert not force.ok
    assert force.reason == CheckpointRejectReason.FORCE_PUSH
    delete = transition_checkpoint_publication(grant, _ctx(delete_ref=True))
    assert not delete.ok
    assert delete.reason == CheckpointRejectReason.REF_DELETE


def test_reject_non_ff_push() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            local_tip_oid=_TIP_OID,
            remote_oid_before="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )
    assert not result.ok
    assert result.reason == CheckpointRejectReason.NON_FF


def test_reject_checkpoint_mixed_with_local_commit() -> None:
    grant = _checkpoint_grant(actions=["checkpoint_publish", "local_commit"])
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.CHECKPOINT_ACTION_MIX


def test_reject_prebound_tip_oid_on_checkpoint_grant() -> None:
    grant = _checkpoint_grant(tip_oid=_TIP_OID)
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.MALFORMED


def test_reject_receipt_as_attestation() -> None:
    grant = _checkpoint_grant(
        owner_attestation_ref="store:bc-c9e9c005/checkpoint-receipt-001.json",
    )
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.ATTESTATION_INVALID


def test_checkpoint_never_emits_green_or_pass() -> None:
    grant = _checkpoint_grant()
    for word in FORBIDDEN_SUCCESS_WORDS:
        result = transition_checkpoint_publication(
            grant,
            _ctx(proposed_success_word=word),
        )
        assert not result.ok
        assert result.reason == CheckpointRejectReason.FABRICATED_SUCCESS
        assert result.phase == CheckpointPhase.BLOCKED_POLICY_VIOLATION


def test_interrupted_push_requires_reconcile_not_fabricated_success() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            local_tip_oid=_TIP_OID,
            interrupted_mid_push=True,
            remote_push_acknowledged=False,
        ),
    )
    assert not result.ok
    assert result.phase == CheckpointPhase.OWNER_ATTENTION
    assert result.reason == CheckpointRejectReason.REMOTE_UNCERTAIN


def test_idempotent_noop_when_already_published() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            worktree_clean=True,
            local_equals_remote=True,
            local_tip_oid=_TIP_OID,
            remote_oid_before=_TIP_OID,
        ),
    )
    assert result.ok
    assert result.phase == CheckpointPhase.CHECKPOINT_IDEMPOTENT_NOOP


def test_published_requires_remote_oid_match() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            local_tip_oid=_TIP_OID,
            remote_push_acknowledged=True,
            remote_oid_after=_TIP_OID,
        ),
    )
    assert result.ok
    assert result.phase == CheckpointPhase.CHECKPOINT_PUBLISHED


def test_valid_checkpoint_grant_cloud_parent() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert result.ok


def test_staging_branch_name_not_protected_stage() -> None:
    grant = _checkpoint_grant(ref="refs/heads/cursor/staging-fix-771f")
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert result.ok
