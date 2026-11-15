"""Temporal-facing IQC policy binding (read-only)."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import sys
from pathlib import Path

_REPO_ROOT = project_root()
_IQC_DIR = resource_path(_REPO_ROOT, ".cursor/pipeline/incremental-quality-coverage")
if str(_IQC_DIR) not in sys.path:
    sys.path.insert(0, str(_IQC_DIR))

from iqc_plan_supersession import IQC_POLICY_REVISION, live_policy_binding, policy_revision_block_reason  # noqa: E402

__all__ = [
    "IQC_POLICY_REVISION",
    "live_policy_binding",
    "policy_revision_block_reason",
]
