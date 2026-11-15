"""Pytest: добавить ``opsx_runtime`` в ``sys.path`` для ``workflows.*`` imports."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
