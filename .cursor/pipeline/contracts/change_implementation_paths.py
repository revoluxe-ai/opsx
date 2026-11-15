"""Change-required production paths — bounded-verify input manifest authority."""

from __future__ import annotations

import sys
from pathlib import Path


def _bounded_verify_root() -> Path:
    return Path(__file__).resolve().parents[1] / "bounded-verify"


def derive_change_required_production_paths(
    repo_root: Path,
    change_id: str,
) -> list[str]:
    """In-policy product ``.py`` paths for ``change=<id>`` (git/worktree + tasks.md)."""

    bv = _bounded_verify_root()
    if str(bv) not in sys.path:
        sys.path.insert(0, str(bv))
    from bounded_verify_input_manifest import collect_input_manifest  # noqa: E402
    from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402

    try:
        manifest = collect_input_manifest(change_id, root=repo_root.resolve())
    except BoundedVerifyPlanError:
        return []
    return sorted(p for p in manifest.product_paths if p.endswith(".py"))
