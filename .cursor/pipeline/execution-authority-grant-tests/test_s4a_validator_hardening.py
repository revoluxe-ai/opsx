"""S4a regression: E1, R1, S1, FF-prefix (OPSX-2 validator hardening)."""

from __future__ import annotations

from typing import Any

import pytest

from checkpoint_publication_v0 import (
    CheckpointPhase,
    CheckpointRejectReason,
    transition_checkpoint_publication,
)
from conftest import VALIDATION_NOW, gate_derived_invocation
from execution_authority_trust_v0 import is_fast_forward_ancestor
from minimal_authority_grant import (
    GrantRejectReason,
    InvocationSurface,
    validate_minimal_authority_grant_v0,
)
from test_checkpoint_publication_v0 import _checkpoint_grant, _ctx

_AGENT = "bc-00f84b95-540f-590e-b857-81003c8927f0"
_TIP_OID = "f7d843452e74f8256ffa50f0355ddcc8aa85d3a5"


@pytest.mark.parametrize(
    "bad_ref",
    [
        "refs/heads/dev",
        "refs/heads/mvp",
        "refs/heads/deploy/avito-unread-staging-release",
        "refs/heads/feature/core-crm",
        "refs/heads/Main",
        "refs/heads/cursor/../main",
    ],
)
def test_r1_checkpoint_ref_allow_list_rejects_non_cursor(bad_ref: str) -> None:
    grant = _checkpoint_grant(ref=bad_ref)
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(),
    )
    assert not result.ok
    assert result.reason in {
        GrantRejectReason.MALFORMED,
        GrantRejectReason.PROTECTED_REF,
    }


def test_s1_caller_surface_without_gate_match_rejected() -> None:
    grant = _checkpoint_grant()
    gate = gate_derived_invocation(surface=InvocationSurface.CLOUD_PARENT)
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.VALIDATION_ONLY,
        now=VALIDATION_NOW,
        gate=gate,
    )
    assert not result.ok
    assert "CHECKPOINT_REJECTED_UNAUTHORIZED_SURFACE" in str(result.reason)


def test_s1_transition_requires_gate_binding() -> None:
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(gate=None),
    )
    assert not result.ok
    assert result.phase == CheckpointPhase.GRANT_REJECTED


def test_ff_prefix_heuristic_rejected_without_ancestry_f1() -> None:
    remote_prefix = _TIP_OID[:8]
    assert remote_prefix != _TIP_OID
    assert not is_fast_forward_ancestor(remote_prefix, _TIP_OID, is_ancestor=None)
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            local_tip_oid=_TIP_OID,
            remote_oid_before=remote_prefix,
        ),
        is_ancestor=None,
    )
    assert not result.ok
    assert result.reason == CheckpointRejectReason.NON_FF


def test_ff_identical_abbreviated_oids_rejected_f1() -> None:
    abbrev = _TIP_OID[:8]
    assert not is_fast_forward_ancestor(abbrev, abbrev, is_ancestor=None)
    assert not is_fast_forward_ancestor(
        abbrev,
        abbrev,
        is_ancestor=lambda _a, _b: True,
    )


def test_ff_abbrev_remote_rejects_injected_positive_ancestry_f1() -> None:
    remote_prefix = _TIP_OID[:8]
    assert not is_fast_forward_ancestor(
        remote_prefix,
        _TIP_OID,
        is_ancestor=lambda _a, _b: True,
    )


def test_idempotent_rejects_abbreviated_oid_equality_f1() -> None:
    abbrev = _TIP_OID[:8]
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            worktree_clean=True,
            local_equals_remote=True,
            local_tip_oid=abbrev,
            remote_oid_before=abbrev,
        ),
    )
    assert result.phase != CheckpointPhase.CHECKPOINT_IDEMPOTENT_NOOP
    assert not result.ok
    assert result.reason == CheckpointRejectReason.NON_FF


def test_idempotent_rejects_forged_flags_without_exact_oid_match_f1() -> None:
    grant = _checkpoint_grant()
    other = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            worktree_clean=True,
            local_equals_remote=True,
            local_tip_oid=_TIP_OID,
            remote_oid_before=other,
        ),
    )
    assert result.phase != CheckpointPhase.CHECKPOINT_IDEMPOTENT_NOOP


def test_ff_accepts_injected_ancestry_predicate() -> None:
    remote = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    local = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    grant = _checkpoint_grant()
    result = transition_checkpoint_publication(
        grant,
        _ctx(
            local_tip_oid=local,
            remote_oid_before=remote,
        ),
        is_ancestor=lambda old, new: old == remote and new == local,
    )
    assert result.ok
    assert result.phase == CheckpointPhase.CHECKPOINT_PUSHING


def test_checkpoint_missing_gate_on_grant_validation() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=None,
    )
    assert not result.ok
    assert result.reason == GrantRejectReason.MALFORMED


def test_alternate_repo_remote_rejected() -> None:
    grant = _checkpoint_grant()
    result = validate_minimal_authority_grant_v0(
        grant,
        surface=InvocationSurface.CLOUD_PARENT,
        now=VALIDATION_NOW,
        gate=gate_derived_invocation(
            repo_remote="https://github.com/other-org/other-repo.git",
        ),
    )
    assert not result.ok
    assert "CHECKPOINT_REJECTED_ALTERNATE_REMOTE" in str(result.reason)
