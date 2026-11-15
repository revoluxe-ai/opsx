"""Pytest hooks for architecture-evidence regression (isolated from product dev defaults)."""

from __future__ import annotations

import os

# Enable openspec_guard → planning_gate wiring only inside this test package.
os.environ.setdefault("OPSX_ARCHITECTURE_EVIDENCE_GATES", "1")
