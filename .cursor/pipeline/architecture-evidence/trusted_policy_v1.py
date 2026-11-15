#!/usr/bin/env python3
"""Pinned S4A trust policy — compiled into verifier; not supplied by Change under review.

Политика доверия загружается только из этого модуля (рядом с oracle), а не из
JSON в PR. CI MUST checkout trusted revision of verifier before applying policy.
"""

from __future__ import annotations

TRUST_POLICY_IMPLEMENTATION_ID = "architecture-evidence-trust-policy-v1"

CANONICAL_REPOSITORY = "github.com/revoluxe-ai/stroy-system"

# S4B: live custodian issuance workflows (S4A verifies contract only).
PRODUCTION_ACQUISITION_WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/opsx-architecture-evidence-custodian-acquisition.yml",
    }
)
PRODUCTION_REVIEW_WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/opsx-architecture-evidence-custodian-review.yml",
    }
)

# Isolated pytest custodian — never accepted in production trust_policy.
TEST_CUSTODIAN_ACQUISITION_WORKFLOW = (
    ".github/workflows/_test_only/architecture-evidence-test-custodian-acquisition.yml"
)
TEST_CUSTODIAN_REVIEW_WORKFLOW = (
    ".github/workflows/_test_only/architecture-evidence-test-custodian-review.yml"
)

ALLOWED_WORKFLOW_REF_PREFIXES = (
    "refs/heads/main",
    "refs/tags/arch-evidence-trust-v",
)

# Actors matching these are never independent reviewers (metadata guard only).
FORBIDDEN_REVIEWER_ACTOR_PREFIXES = ("github-actions[bot]",)


def acquisition_workflows_for_mode(trust_policy: str) -> frozenset[str]:
    if trust_policy == "test_custodian":
        return frozenset({TEST_CUSTODIAN_ACQUISITION_WORKFLOW})
    return PRODUCTION_ACQUISITION_WORKFLOW_PATHS


def review_workflows_for_mode(trust_policy: str) -> frozenset[str]:
    if trust_policy == "test_custodian":
        return frozenset({TEST_CUSTODIAN_REVIEW_WORKFLOW})
    return PRODUCTION_REVIEW_WORKFLOW_PATHS
