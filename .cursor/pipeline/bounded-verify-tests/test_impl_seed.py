# -*- coding: utf-8 -*-
"""Hermetic IMPL_OK projection for bounded-verify tests."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"


def enable_hermetic_impl_env() -> None:
    os.environ["BOUNDED_VERIFY_HERMETIC_IMPL"] = "1"


def seed_hermetic_impl_ok(change_id: str, *, root: Path) -> None:
    """Bind IMPL_OK for freeze tests without split-APPLY disk fixtures."""
    import shutil

    enable_hermetic_impl_env()
    policy_sources = (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        "opsx/project/capabilities.yaml",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
    )
    for rel in policy_sources:
        src = REPO_ROOT / rel
        if src.is_file():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    token_src = Path(__file__).resolve().parent / "fixtures" / "test-authority.token"
    token_dest = root / ".cursor/pipeline/bounded-verify-tests/fixtures/test-authority.token"
    token_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(token_src, token_dest)
    import sys

    sys.path.insert(0, str(BV_ROOT))
    from bounded_verify_impl_completion import write_hermetic_impl_projection

    write_hermetic_impl_projection(change_id, root=root)
