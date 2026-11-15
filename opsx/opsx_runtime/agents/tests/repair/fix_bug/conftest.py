"""Pytest: ``agents`` и ``model_config`` на ``sys.path`` для policy-тестов fix_bug."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME_ROOT = Path(__file__).resolve().parents[4]
_CONFIGS_ROOT = _RUNTIME_ROOT.parent / "configs"
for root in (_RUNTIME_ROOT, _CONFIGS_ROOT):
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
