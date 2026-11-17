"""Pytest: ``agents`` и ``opsx`` на ``sys.path`` для deterministic tool tests."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[4]
_OPSX = _RUNTIME.parent
for entry in (_OPSX, _RUNTIME):
    text = str(entry)
    if text not in sys.path:
        sys.path.insert(0, text)