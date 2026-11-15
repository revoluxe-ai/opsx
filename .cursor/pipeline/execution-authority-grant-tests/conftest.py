"""Pytest path setup for execution-authority-grant contract tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "execution-authority-grant"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution_authority_trust_v0 import GateDerivedInvocation
from minimal_authority_grant import InvocationSurface

VALIDATION_NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
_AGENT_DEFAULT = "bc-00f84b95-540f-590e-b857-81003c8927f0"
CANONICAL_REPO_REMOTE = "https://github.com/revoluxe-ai/stroy-system.git"


def gate_derived_invocation(
    *,
    surface: InvocationSurface = InvocationSurface.CLOUD_PARENT,
    agent_id: str = _AGENT_DEFAULT,
    mint_channel: str = "owner",
    repo_remote: str = CANONICAL_REPO_REMOTE,
) -> GateDerivedInvocation:
    """Gate-derived binding для checkpoint contract-тестов (не worker-asserted)."""

    return GateDerivedInvocation(
        surface=surface,
        mint_channel=mint_channel,
        repo_remote=repo_remote,
        agent_id=agent_id,
    )
